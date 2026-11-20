"""Contextual Hybrid Retriever combining SQLite FTS5 (BM25), FastEmbed In-Memory Matrix, RRF, and FlashRank.

File: backend/src/rag/retriever.py
Purpose: Implements optimized Anthropic & OpenAI hybrid retrieval with sub-250ms latency:
         1. Lexical BM25 search via SQLite FTS5 (Top-25) over reusable WAL connection
         2. Dense semantic search via FastEmbed ONNX (BAAI/bge-small-en-v1.5) + Contiguous In-Memory NumPy Matrix (Top-25)
         3. Concurrent multi-threaded execution of BM25 and Dense search
         4. Reciprocal Rank Fusion (RRF, k=60)
         5. Cross-encoder reranking via FlashRank ONNX (ms-marco-MiniLM-L-12-v2) to Top-K (bounded passages)
         6. Just-in-Time (JIT) parent document expansion.
"""

from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import pickle
import re
import sqlite3
import sys
from typing import Any, Dict, List, Optional
from fastembed import TextEmbedding
from flashrank import Ranker, RerankRequest
import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parents[3]
INDEX_DIR = PROJECT_ROOT / "data" / "index"
RAG_DB_PATH = INDEX_DIR / "rag_index.db"
EMBEDDINGS_NPZ = INDEX_DIR / "chunk_embeddings.npz"
STORAGE_SQLITE = INDEX_DIR / "qdrant" / "collection" / "real_estate_chunks" / "storage.sqlite"

# Singletons for zero-reallocation steady-state performance
_shared_embedder: Optional[TextEmbedding] = None
_shared_ranker: Optional[Ranker] = None
_shared_matrix: Optional[np.ndarray] = None
_shared_chunk_ids: Optional[np.ndarray] = None
_shared_payloads: Optional[List[Dict[str, Any]]] = None
_shared_db_conn: Optional[sqlite3.Connection] = None
_warmed_up: bool = False


def get_shared_embedder() -> TextEmbedding:
    """Returns singleton FastEmbed TextEmbedding configured with CPU thread optimization."""
    global _shared_embedder
    if _shared_embedder is None:
        cache_dir = Path.home() / ".cache" / "fastembed"
        cache_dir.mkdir(parents=True, exist_ok=True)
        _shared_embedder = TextEmbedding(
            model_name="BAAI/bge-small-en-v1.5",
            threads=2,
            cache_dir=str(cache_dir),
        )
    return _shared_embedder


def get_shared_ranker() -> Ranker:
    """Returns singleton FlashRank Ranker configured with local cache and lightweight ONNX model."""
    global _shared_ranker
    if _shared_ranker is None:
        model_name = os.getenv("RAG_RERANKER_MODEL", "ms-marco-TinyBERT-L-2-v2")
        cache_dir = Path.home() / ".cache" / "flashrank"
        cache_dir.mkdir(parents=True, exist_ok=True)
        _shared_ranker = Ranker(
            model_name=model_name,
            cache_dir=str(cache_dir),
        )
    return _shared_ranker


def get_shared_db_conn(db_path: Path = RAG_DB_PATH) -> sqlite3.Connection:
    """Returns a reusable read-only SQLite connection in WAL mode."""
    global _shared_db_conn
    if _shared_db_conn is None:
        if not db_path.exists():
            raise FileNotFoundError(f"RAG DB not found at {db_path}")
        # Enable WAL mode on master
        setup_conn = sqlite3.connect(str(db_path))
        setup_conn.execute("PRAGMA journal_mode = WAL;")
        setup_conn.execute("PRAGMA synchronous = NORMAL;")
        setup_conn.close()

        # Connect with read-only URI
        uri_path = f"file:{db_path.resolve().as_posix()}?mode=ro"
        conn = sqlite3.connect(uri_path, uri=True, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only = ON;")
        conn.execute("PRAGMA mmap_size = 268435456;")  # 256MB mmap
        _shared_db_conn = conn
    return _shared_db_conn


def load_in_memory_embeddings() -> tuple[np.ndarray, np.ndarray, List[Dict[str, Any]]]:
    """Loads pre-computed normalized float32 chunk embeddings into memory (325 chunks = 487.5 KB)."""
    global _shared_matrix, _shared_chunk_ids, _shared_payloads
    if _shared_matrix is not None and _shared_chunk_ids is not None and _shared_payloads is not None:
        return _shared_matrix, _shared_chunk_ids, _shared_payloads

    if EMBEDDINGS_NPZ.exists():
        data = np.load(str(EMBEDDINGS_NPZ), allow_pickle=True)
        _shared_matrix = data["matrix"]
        _shared_chunk_ids = data["chunk_ids"]
        _shared_payloads = list(data["payloads"])
        return _shared_matrix, _shared_chunk_ids, _shared_payloads

    # Fallback: Extract from storage.sqlite once and cache to .npz
    if STORAGE_SQLITE.exists():
        conn = sqlite3.connect(f"file:{STORAGE_SQLITE}?mode=ro", uri=True)
        cur = conn.cursor()
        cur.execute("SELECT id, point FROM points")
        rows = cur.fetchall()
        conn.close()

        cids = []
        vecs = []
        pls = []
        for r in rows:
            pt = pickle.loads(r[1])
            cid = pt.payload.get("chunk_id") if pt.payload else str(r[0])
            cids.append(cid)
            vecs.append(pt.vector)
            pls.append(pt.payload or {})

        mat = np.array(vecs, dtype=np.float32)
        norms = np.linalg.norm(mat, axis=1, keepdims=True)
        mat = mat / np.maximum(norms, 1e-9)

        np.savez_compressed(
            str(EMBEDDINGS_NPZ),
            matrix=mat,
            chunk_ids=np.array(cids),
            payloads=np.array(pls, dtype=object),
        )
        _shared_matrix = mat
        _shared_chunk_ids = np.array(cids)
        _shared_payloads = pls
        return _shared_matrix, _shared_chunk_ids, _shared_payloads

    raise FileNotFoundError("Neither chunk_embeddings.npz nor storage.sqlite was found to load vectors.")


class RetrievedChunk:
    """Encapsulates a ranked retrieval hit with full provenance."""

    def __init__(
        self,
        chunk_id: str,
        doc_id: str,
        title: str,
        heading: str,
        full_text: str,
        content: str,
        source_url: str,
        source_platform: str,
        doc_category: str,
        country: str,
        city: str,
        linked_entity_id: Optional[str],
        score: float,
        rrf_score: float = 0.0,
    ):
        self.chunk_id = chunk_id
        self.doc_id = doc_id
        self.title = title
        self.heading = heading
        self.full_text = full_text
        self.content = content
        self.source_url = source_url
        self.source_platform = source_platform
        self.doc_category = doc_category
        self.country = country
        self.city = city
        self.linked_entity_id = linked_entity_id
        self.score = score
        self.rrf_score = rrf_score

    def to_dict(self) -> Dict[str, Any]:
        return {
            "chunk_id": self.chunk_id,
            "doc_id": self.doc_id,
            "title": self.title,
            "heading": self.heading,
            "full_text": self.full_text,
            "content": self.content,
            "source_url": self.source_url,
            "source_platform": self.source_platform,
            "doc_category": self.doc_category,
            "country": self.country,
            "city": self.city,
            "linked_entity_id": self.linked_entity_id,
            "score": round(self.score, 4),
            "rrf_score": round(self.rrf_score, 6),
        }


class HybridRetriever:
    """Production-grade hybrid retriever combining sparse BM25, in-memory dense ONNX vectors, RRF, and FlashRank."""

    def __init__(
        self,
        rag_db_path: Path = RAG_DB_PATH,
        collection_name: str = "real_estate_chunks",
    ):
        self.rag_db_path = rag_db_path
        self.collection_name = collection_name
        self.embeddings_matrix, self.chunk_ids, self.payloads = load_in_memory_embeddings()
        self.chunk_lookup: Dict[str, Dict[str, Any]] = {
            cid: self.payloads[i] for i, cid in enumerate(self.chunk_ids)
        }

    @property
    def embedder(self) -> TextEmbedding:
        return get_shared_embedder()

    @property
    def ranker(self) -> Ranker:
        return get_shared_ranker()

    def warmup(self) -> None:
        """Pre-warms FastEmbed, FlashRank, SQLite, and in-memory matrix so the first query pays zero cold-start."""
        global _warmed_up
        if _warmed_up:
            return
        try:
            # 1. Warmup FastEmbed ONNX session
            q_emb = list(self.embedder.embed(["warmup"]))[0]
            q_vec = np.array(q_emb, dtype=np.float32)
            _ = self.embeddings_matrix @ q_vec

            # 2. Warmup FlashRank ONNX session
            dummy_passages = [{"id": "warmup_0", "text": "DarGlobal real estate tokenization warmup."}]
            _ = self.ranker.rerank(RerankRequest(query="warmup", passages=dummy_passages))

            # 3. Warmup SQLite connection
            conn = get_shared_db_conn(self.rag_db_path)
            conn.execute("SELECT 1;").fetchone()

            _warmed_up = True
        except Exception as e:
            print(f"Warning: RAG Warmup encountered: {e}")

    def search_bm25(
        self,
        query: str,
        limit: int = 25,
        platform: Optional[str] = None,
        country: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Executes full-text lexical search on SQLite FTS5 with connection reuse."""
        clean_tokens = re.findall(r"\w+", query)
        if not clean_tokens:
            return []

        fts_query = " OR ".join(clean_tokens)
        conn = get_shared_db_conn(self.rag_db_path)
        cursor = conn.cursor()

        sql = """
        SELECT c.*, bm25(chunks_fts) AS bm25_score
        FROM chunks_fts f
        JOIN chunks c ON f.chunk_id = c.chunk_id
        WHERE chunks_fts MATCH ?
        """
        params: List[Any] = [fts_query]

        if platform:
            sql += " AND c.source_platform = ?"
            params.append(platform)
        if country:
            sql += " AND LOWER(c.country) = LOWER(?)"
            params.append(country)

        sql += " ORDER BY bm25_score ASC LIMIT ?"
        params.append(limit)

        try:
            cursor.execute(sql, params)
            rows = cursor.fetchall()
            return [dict(r) for r in rows]
        except Exception as e:
            print(f"BM25 Search Error: {e}")
            return []

    def search_dense(
        self,
        query: str,
        limit: int = 25,
        platform: Optional[str] = None,
        country: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Executes dense vector search via in-memory matrix multiplication (embeddings_matrix @ q_vec)."""
        try:
            # 1. Embed query with FastEmbed ONNX (~8ms)
            q_emb = list(self.embedder.embed([query]))[0]
            q_vec = np.array(q_emb, dtype=np.float32)
            q_norm = np.linalg.norm(q_vec)
            if q_norm > 1e-9:
                q_vec /= q_norm

            # 2. Vectorized cosine similarity dot product (< 0.2ms)
            scores = self.embeddings_matrix @ q_vec

            # 3. Optional metadata filtering
            valid_indices = None
            if platform or country:
                valid_indices = []
                for idx, payload in enumerate(self.payloads):
                    if platform and payload.get("source_platform") != platform:
                        continue
                    if country and str(payload.get("country", "")).lower() != country.lower():
                        continue
                    valid_indices.append(idx)
                if not valid_indices:
                    return []
                scores = scores[valid_indices]
                chunk_indices = np.array(valid_indices)
            else:
                chunk_indices = np.arange(len(self.chunk_ids))

            # 4. Top-K Selection
            k = min(limit, len(scores))
            if k <= 0:
                return []

            top_k_partition = np.argpartition(scores, -k)[-k:]
            top_k_sorted = top_k_partition[np.argsort(scores[top_k_partition])[::-1]]

            hits = []
            for idx in top_k_sorted:
                real_idx = chunk_indices[idx]
                payload = dict(self.payloads[real_idx])
                payload["dense_score"] = float(scores[idx])
                hits.append(payload)

            return hits
        except Exception as e:
            print(f"Dense Search Error: {e}")
            return []

    def search(
        self,
        query: str,
        top_k: int = 4,
        platform: Optional[str] = None,
        country: Optional[str] = None,
        category: Optional[str] = None,
        rrf_k: int = 60,
    ) -> List[RetrievedChunk]:
        """Concurrent Hybrid RAG pipeline: BM25 + Dense -> RRF -> FlashRank Cross-Encoder Rerank."""
        # 1. Retrieve candidates concurrently from both modalities (~10-15ms)
        with ThreadPoolExecutor(max_workers=2) as executor:
            future_bm25 = executor.submit(self.search_bm25, query, 25, platform, country)
            future_dense = executor.submit(self.search_dense, query, 25, platform, country)
            bm25_hits = future_bm25.result()
            dense_hits = future_dense.result()

        # 2. Reciprocal Rank Fusion (RRF, k=60)
        rrf_scores: Dict[str, float] = {}
        chunk_lookup: Dict[str, Dict[str, Any]] = {}

        for rank, hit in enumerate(bm25_hits):
            cid = hit["chunk_id"]
            rrf_scores[cid] = rrf_scores.get(cid, 0.0) + (1.0 / (rrf_k + rank + 1))
            chunk_lookup[cid] = hit

        for rank, hit in enumerate(dense_hits):
            cid = hit["chunk_id"]
            rrf_scores[cid] = rrf_scores.get(cid, 0.0) + (1.0 / (rrf_k + rank + 1))
            if cid not in chunk_lookup:
                chunk_lookup[cid] = hit

        if not chunk_lookup:
            return []

        # Sort top 12 fused candidates by RRF score
        sorted_cids = sorted(rrf_scores.keys(), key=lambda x: rrf_scores[x], reverse=True)[:12]
        candidate_chunks = [chunk_lookup[cid] for cid in sorted_cids]

        # 3. FlashRank Cross-Encoder Reranking with bounded passage length (800 chars)
        passages = [
            {
                "id": c["chunk_id"],
                "text": (c.get("content") or c.get("full_text") or "")[:800],
            }
            for c in candidate_chunks
        ]
        rerank_req = RerankRequest(query=query, passages=passages)
        reranked_results = self.ranker.rerank(rerank_req)

        # 4. Assemble final top_k RetrievedChunk objects
        final_chunks: List[RetrievedChunk] = []
        for r in reranked_results[:top_k]:
            cid = r["id"]
            data = chunk_lookup[cid]
            final_chunks.append(
                RetrievedChunk(
                    chunk_id=cid,
                    doc_id=data.get("doc_id", ""),
                    title=data.get("title", ""),
                    heading=data.get("heading", ""),
                    full_text=data.get("full_text", ""),
                    content=data.get("content", ""),
                    source_url=data.get("source_url", ""),
                    source_platform=data.get("source_platform", ""),
                    doc_category=data.get("doc_category", ""),
                    country=data.get("country", ""),
                    city=data.get("city", ""),
                    linked_entity_id=data.get("linked_entity_id"),
                    score=float(r["score"]),
                    rrf_score=rrf_scores.get(cid, 0.0),
                )
            )

        return final_chunks

    def read_document(self, doc_id: str) -> Optional[Dict[str, Any]]:
        """Just-in-Time (JIT) full document retrieval for deep-dive context."""
        conn = get_shared_db_conn(self.rag_db_path)
        cursor = conn.cursor()

        cursor.execute(
            """
            SELECT * FROM chunks WHERE doc_id = ? OR chunk_id = ?
            ORDER BY chunk_id ASC;
            """,
            (doc_id, doc_id),
        )
        rows = cursor.fetchall()

        if not rows:
            return None

        first = dict(rows[0])
        combined_text = "\n\n".join(r["content"] for r in rows)

        return {
            "doc_id": doc_id,
            "title": first.get("title"),
            "source_platform": first.get("source_platform"),
            "doc_category": first.get("doc_category"),
            "country": first.get("country"),
            "city": first.get("city"),
            "source_url": first.get("source_url"),
            "total_chunks": len(rows),
            "full_content": combined_text,
        }
