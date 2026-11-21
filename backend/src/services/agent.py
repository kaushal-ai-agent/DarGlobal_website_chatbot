"""Agentic LLM service using Google ADK Real Estate Advisor.

File: backend/src/services/agent.py
Purpose: Connects FastAPI non-streaming chat route to ADKRunnerService.
"""

import json
import uuid
from typing import List, Optional

from backend.src.models.schemas import ChatRequest, ChatResponse, PropertyItem
from backend.src.services.adk_runner import adk_runner_service
from backend.src.utils.logger import get_logger

logger = get_logger("services.agent")


class AgentService:
    """Orchestrates query execution through Google ADK real_estate_advisor."""

    def __init__(self, runner_service=None):
        self.adk_service = runner_service or adk_runner_service

    async def process_chat(self, request: ChatRequest) -> ChatResponse:
        """Processes an incoming chat message with Google ADK real_estate_advisor."""
        conv_id = request.conversation_id or str(uuid.uuid4())
        logger.info("Processing non-streaming chat turn for conversation %s: %s", conv_id, request.message)

        reply_text = ""
        matched_items: List[PropertyItem] = []

        # Consume the ADK runner stream to produce the non-streaming response
        async for sse_line in self.adk_service.run_stream(
            message=request.message,
            session_id=conv_id,
            streaming=False,
        ):
            line_str = sse_line.strip()
            if not line_str or line_str.startswith(":"):
                continue
            if line_str.startswith("data: "):
                try:
                    payload = json.loads(line_str[6:])
                    ev_type = payload.get("type")
                    if ev_type == "final_response":
                        reply_text = payload.get("text", "")
                    elif ev_type == "tool_result":
                        preview = payload.get("response_preview", {})
                        if isinstance(preview, dict):
                            results = preview.get("results") or preview.get("rows") or []
                            for r in results:
                                if isinstance(r, dict) and r.get("title"):
                                    matched_items.append(
                                        PropertyItem(
                                            id=r.get("listing_id") or r.get("project_id") or str(uuid.uuid4())[:8],
                                            source="darglobal" if "project" in str(r).lower() else "wasalt",
                                            title=r.get("title", "Property"),
                                            location=r.get("location") or "Saudi Arabia",
                                            price_formatted=r.get("price") or r.get("pricing"),
                                            source_url=r.get("url") or "https://wasalt.sa",
                                        )
                                    )
                except Exception as parse_err:
                    logger.debug("Non-streaming SSE parse skip: %s", parse_err)

        if not reply_text:
            reply_text = "I have processed your inquiry regarding DarGlobal and Wasalt real estate."

        return ChatResponse(
            reply=reply_text,
            conversation_id=conv_id,
            matched_properties=matched_items,
        )
