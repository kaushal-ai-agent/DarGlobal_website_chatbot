"""Agentic LLM service integrating Google ADK Agent Harness, LiteLLM, and Hybrid RAG.

File: backend/src/services/agent.py
Purpose: Connects FastAPI chat route to the production-grade AgentHarness.
Core Logic: Processes user queries, executes lifecycle callbacks, invokes Poka-Yoke tools,
            and converts results into standard API ChatResponse models.
"""

import uuid
from typing import List, Optional

from backend.src.agent.harness import AgentHarness
from backend.src.models.schemas import ChatRequest, ChatResponse, PropertyItem
from backend.src.services.rag import RAGService
from backend.src.utils.logger import get_logger

logger = get_logger("services.agent")


class AgentService:
    """Orchestrates query classification, retrieval from RAG, and LLM synthesis."""

    def __init__(
        self,
        rag_service: Optional[RAGService] = None,
        harness: Optional[AgentHarness] = None,
    ):
        self.rag_service = rag_service or RAGService()
        self.harness = harness or AgentHarness()

    async def process_chat(self, request: ChatRequest) -> ChatResponse:
        """Processes an incoming chat message with retrieval and LLM response generation."""
        conv_id = request.conversation_id or str(uuid.uuid4())
        logger.info("Processing chat turn for conversation %s: %s", conv_id, request.message)

        # Execute query via AgentHarness
        history_dicts = [{"role": m.role, "content": m.content} for m in request.history]
        result = await self.harness.process_query(
            user_message=request.message,
            session_id=conv_id,
            history=history_dicts,
        )

        # Convert matched records to standard PropertyItem models
        matched_items: List[PropertyItem] = []
        for rec in result.get("matched_properties", []):
            try:
                # Handle Wasalt listing
                if "listing_id" in rec:
                    matched_items.append(
                        PropertyItem(
                            id=rec["listing_id"],
                            source="wasalt",
                            title=rec.get("title", "Property Listing"),
                            location=rec.get("location", "Riyadh, Saudi Arabia"),
                            price_formatted=rec.get("price_sar"),
                            price_numerical=rec.get("price_amount"),
                            currency="SAR",
                            property_type=rec.get("property_type"),
                            bedrooms=rec.get("bedrooms"),
                            bathrooms=rec.get("bathrooms"),
                            source_url=rec.get("listing_url", "https://wasalt.sa"),
                            metadata={
                                "fal_license": rec.get("fal_license"),
                                "rega_ad_license": rec.get("rega_ad_license"),
                                "rega_status": rec.get("rega_status"),
                            },
                        )
                    )
                # Handle DarGlobal project
                elif "project_id" in rec:
                    matched_items.append(
                        PropertyItem(
                            id=rec["project_id"],
                            source="darglobal",
                            title=rec.get("project_name", "Luxury Project"),
                            location=rec.get("location", "Global"),
                            price_formatted=rec.get("pricing", "Price upon inquiry"),
                            currency="USD",
                            description=rec.get("summary"),
                            status=rec.get("completion"),
                            source_url=rec.get("project_url", "https://darglobal.co.uk"),
                            metadata={
                                "brand_partner": rec.get("brand_partner"),
                                "residency_eligible": rec.get("residency_eligible"),
                            },
                        )
                    )
            except Exception as e:
                logger.warning("Failed to serialize matched property record: %s", e)

        return ChatResponse(
            reply=result["reply"],
            conversation_id=conv_id,
            matched_properties=matched_items,
        )
