"""Tools for Company Knowledge Agent."""

from typing import Any, Dict, List, Optional
from backend.src.rag.retriever import HybridRetriever

_retriever: Optional[HybridRetriever] = None


def get_retriever() -> HybridRetriever:
    """Singleton getter for HybridRetriever with pre-warming."""
    global _retriever
    if _retriever is None:
        _retriever = HybridRetriever()
        _retriever.warmup()
    return _retriever


def search_knowledge_base(query: str, top_k: int = 4) -> Dict[str, Any]:
    """Searches the 88 Markdown knowledge documents covering DarGlobal corporate background,

    brand collaborations (Trump, Pagani, Aston Martin, Elie Saab), tokenization,
    residency & golden visa rules, and Wasalt platform/auction guidelines.

    Uses Hybrid RAG: Sparse FTS5 BM25 + Dense FastEmbed ONNX + FlashRank cross-encoder reranking.

    Args:
        query: The natural language search query (e.g. 'Trump Tower Jeddah seafront details',
               'How does DarGlobal tokenization work', 'Infath auction bidding deposit rules').
        top_k: Number of high-relevance snippets to retrieve (default 4, max 8).

    Returns:
        Dictionary containing matched document snippets, titles, categories, and canonical URLs.
    """
    top_k = min(max(1, top_k), 8)
    retriever = get_retriever()
    hits = retriever.search(query=query.strip(), top_k=top_k)

    results: List[Dict[str, Any]] = []
    for h in hits:
        results.append({
            "doc_id": h.doc_id,
            "title": h.title,
            "heading": h.heading,
            "category": h.doc_category,
            "platform": h.source_platform,
            "snippet": h.content,
            "source_url": h.source_url,
            "relevance_score": round(h.score, 4),
        })

    return {
        "query": query,
        "count": len(results),
        "results": results,
    }


def read_document(doc_id: str) -> Dict[str, Any]:
    """Retrieves the full text of a specific Markdown knowledge document for deep reading.

    Use this when a user asks for complete chapters, step-by-step guides, or comprehensive
    background on an entity previously discovered via search_knowledge_base.

    Args:
        doc_id: Unique document identifier (e.g. 'dg_03_why_invest_and_residency_guides',
                'ws_04_auctions_guide_infath_and_ewallet', 'dg_proj_trump_tower_jeddah').

    Returns:
        Full document text, title, source URL, and category metadata.
    """
    retriever = get_retriever()
    doc = retriever.read_document(doc_id.strip())
    if not doc:
        return {
            "found": False,
            "doc_id": doc_id,
            "error": f"Document '{doc_id}' not found in knowledge registry.",
        }

    return {
        "found": True,
        "doc_id": doc_id,
        "title": doc.get("title", ""),
        "source_url": doc.get("source_url", ""),
        "category": doc.get("category", ""),
        "content": doc.get("content", ""),
    }
