"""Standalone Benchmark Script for Hybrid RAG search_knowledge_base.

Measures Cold Start vs Warm Call and stage-by-stage latency breakdowns:
- Initialization (FastEmbed, FlashRank, in-memory embeddings matrix, SQLite WAL)
- FTS5 BM25 search
- FastEmbed query embedding + Dense vector search
- FlashRank cross-encoder reranking
- End-to-end search_knowledge_base call
"""

import time
import sys
from pathlib import Path

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def run_benchmark(query: str = "How does DarGlobal tokenization work", top_k: int = 4):
    print("=" * 70)
    print("HYBRID RAG BENCHMARK: search_knowledge_base")
    print(f"Query: '{query}' | top_k: {top_k}")
    print("=" * 70)

    # 1. Measure Imports
    t_import_start = time.perf_counter()
    from backend.agents.real_estate_advisor.subagents.company_knowledge_agent.tools import (
        search_knowledge_base,
        get_retriever,
    )
    from backend.src.rag.retriever import HybridRetriever, RerankRequest
    t_import = time.perf_counter() - t_import_start
    print(f"\n[0] Module Import Time: {t_import * 1000:.2f} ms")

    # 2. Detailed Stage Breakdown on Call 1 (Cold / Initializing)
    print("\n--- Detailed Stage Breakdown (Call 1: Startup & Cold Inference) ---")
    retriever = HybridRetriever()

    t_init_start = time.perf_counter()
    _ = retriever.embedder
    _ = retriever.ranker
    _ = retriever.embeddings_matrix
    t_init = time.perf_counter() - t_init_start
    print(f"  [1.1] Model & Index Load: {t_init * 1000:.2f} ms ({t_init:.3f} s)")

    t_bm25_start = time.perf_counter()
    bm25_hits = retriever.search_bm25(query=query, limit=25)
    t_bm25 = time.perf_counter() - t_bm25_start
    print(f"  [1.2] FTS5 BM25 Sparse Search (Cold): {t_bm25 * 1000:.2f} ms (Found {len(bm25_hits)} hits)")

    t_embed_start = time.perf_counter()
    query_emb = list(retriever.embedder.embed([query]))[0]
    t_embed = time.perf_counter() - t_embed_start
    print(f"  [1.3] FastEmbed Query Embedding (Cold): {t_embed * 1000:.2f} ms")

    t_dense_start = time.perf_counter()
    dense_hits = retriever.search_dense(query=query, limit=25)
    t_dense = time.perf_counter() - t_dense_start
    print(f"  [1.4] Dense In-Memory Vector Search (Cold): {t_dense * 1000:.2f} ms (Found {len(dense_hits)} hits)")

    # FlashRank cold rerank
    candidate_chunks = [dense_hits[i] for i in range(min(12, len(dense_hits)))]
    passages = [{"id": c["chunk_id"], "text": (c.get("content") or "")[:800]} for c in candidate_chunks]
    t_rerank_start = time.perf_counter()
    _ = retriever.ranker.rerank(RerankRequest(query=query, passages=passages))
    t_rerank = time.perf_counter() - t_rerank_start
    print(f"  [1.5] FlashRank Cross-Encoder Rerank (Cold): {t_rerank * 1000:.2f} ms ({t_rerank:.3f} s)")

    # Measure full search call (Cold end-to-end)
    t_cold_start = time.perf_counter()
    res_cold = search_knowledge_base(query=query, top_k=top_k)
    t_cold_total = time.perf_counter() - t_cold_start
    print(f"  [1.6] Cold search_knowledge_base Full Call: {t_cold_total * 1000:.2f} ms ({t_cold_total:.3f} s)")

    # 3. Detailed Stage Breakdown on Call 2 (Warm / Steady-State)
    print("\n--- Detailed Stage Breakdown (Call 2: Warm Steady-State) ---")
    t_bm25_w_start = time.perf_counter()
    bm25_hits_w = retriever.search_bm25(query=query, limit=25)
    t_bm25_w = time.perf_counter() - t_bm25_w_start
    print(f"  [2.1] FTS5 BM25 Sparse Search: {t_bm25_w * 1000:.2f} ms")

    t_embed_w_start = time.perf_counter()
    query_emb_w = list(retriever.embedder.embed([query]))[0]
    t_embed_w = time.perf_counter() - t_embed_w_start
    print(f"  [2.2] FastEmbed Query Embedding: {t_embed_w * 1000:.2f} ms")

    t_dense_w_start = time.perf_counter()
    dense_hits_w = retriever.search_dense(query=query, limit=25)
    t_dense_w = time.perf_counter() - t_dense_w_start
    print(f"  [2.3] Dense In-Memory Vector Search: {t_dense_w * 1000:.2f} ms")

    # FlashRank warm rerank
    t_rerank_w_start = time.perf_counter()
    _ = retriever.ranker.rerank(RerankRequest(query=query, passages=passages))
    t_rerank_w = time.perf_counter() - t_rerank_w_start
    print(f"  [2.4] FlashRank Cross-Encoder Rerank (12 candidates): {t_rerank_w * 1000:.2f} ms")

    # Measure full search call (Warm end-to-end)
    t_warm_start = time.perf_counter()
    res_warm = search_knowledge_base(query=query, top_k=top_k)
    t_warm_total = time.perf_counter() - t_warm_start
    print(f"  [2.5] Warm search_knowledge_base Full Call: {t_warm_total * 1000:.2f} ms ({t_warm_total:.3f} s)")

    # 4. Output Parity & Ground Truth Verification
    print("\n" + "=" * 70)
    print("OUTPUT VERIFICATION & RETRIEVED HITS:")
    print(f"Total Results: {res_warm.get('count', 0)}")
    for i, r in enumerate(res_warm.get("results", [])):
        print(f"\n  [{i+1}] Title: {r.get('title')}")
        print(f"      Heading: {r.get('heading')}")
        print(f"      Score: {r.get('relevance_score')}")
        print(f"      Category: {r.get('category')} | Platform: {r.get('platform')}")
        print(f"      URL: {r.get('source_url')}")
        snippet_preview = r.get('snippet', '').replace('\n', ' ')[:120]
        print(f"      Snippet: {snippet_preview}...")

    print("\n" + "=" * 70)
    print("SUMMARY LATENCY BREAKDOWN:")
    print(f"  Init Time:     {t_init * 1000:.2f} ms")
    print(f"  BM25 Search:   {t_bm25_w * 1000:.2f} ms")
    print(f"  Dense Search:  {t_dense_w * 1000:.2f} ms (Embedding: {t_embed_w * 1000:.2f} ms + Dot Product: {(t_dense_w - t_embed_w) * 1000:.2f} ms)")
    print(f"  FlashRank:     {t_rerank_w * 1000:.2f} ms")
    print(f"  Cold Total:    {t_cold_total * 1000:.2f} ms ({t_cold_total:.3f} s)")
    print(f"  Warm Total:    {t_warm_total * 1000:.2f} ms ({t_warm_total:.3f} s)")
    print("=" * 70)


if __name__ == "__main__":
    run_benchmark()
