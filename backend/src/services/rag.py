"""RAG retrieval service managing HybridRetriever, FTS5, and vector index.

File: backend/src/services/rag.py
Purpose: Connects backend service layer to the hybrid SQLite FTS5 + FastEmbed + FlashRank pipeline.
Core Logic: Implements dense and lexical hybrid retrieval over DarGlobal and Wasalt datasets.
"""

from typing import List, Optional

from backend.src.models.schemas import PropertyItem
from backend.src.rag.retriever import HybridRetriever
from backend.src.utils.logger import get_logger

logger = get_logger("services.rag")


class RAGService:
    """Manages property document embedding, chunk indexing, and similarity retrieval."""

    def __init__(self, retriever: Optional[HybridRetriever] = None):
        self.retriever = retriever or HybridRetriever()

    async def retrieve_relevant_properties(
        self,
        query: str,
        top_k: int = 5,
        filters: Optional[dict] = None,
    ) -> List[PropertyItem]:
        """Perform hybrid search to retrieve top-K matching properties or knowledge chunks."""
        logger.info("Executing hybrid retrieval for query: '%s' (top_k=%d)", query, top_k)
        platform = filters.get("platform") if filters else None
        country = filters.get("country") if filters else None

        chunks = self.retriever.search(
            query=query,
            top_k=top_k,
            platform=platform,
            country=country,
        )

        items: List[PropertyItem] = []
        for c in chunks:
            items.append(
                PropertyItem(
                    id=c.chunk_id,
                    source="darglobal" if c.source_platform == "darglobal" else "wasalt",
                    title=c.title,
                    location=f"{c.city}, {c.country}",
                    description=c.content[:300] + ("..." if len(c.content) > 300 else ""),
                    source_url=c.source_url,
                    metadata={
                        "heading": c.heading,
                        "doc_id": c.doc_id,
                        "score": c.score,
                    },
                )
            )
        return items

    async def ingest_properties(self, properties: List[PropertyItem]) -> int:
        """Batch ingest placeholder for dynamic API ingestion."""
        logger.info("Ingesting %d properties into index...", len(properties))
        return len(properties)
