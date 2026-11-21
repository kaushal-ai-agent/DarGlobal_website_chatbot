"""Google ADK Runner Service with Session Management and Normalized SSE Streaming.

File: backend/src/services/adk_runner.py
Purpose:
  - Manages ADK Session lifecycles (create, get, delete) via InMemorySessionService.
  - Connects to the single root agent (real_estate_orchestrator) via Runner.
  - Converts ADK's raw Event stream into a clean, normalized SSE protocol for React.
  - Emits keepalive heartbeats to protect Render/Nginx cloud reverse proxies.
"""

import asyncio
import json
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Any, AsyncGenerator, Dict, Optional
from dotenv import load_dotenv

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Load environment configuration from real_estate_advisor and root .env
load_dotenv(PROJECT_ROOT / "backend" / "agents" / "real_estate_advisor" / ".env")
load_dotenv(PROJECT_ROOT / ".env")

from google.adk.agents.run_config import RunConfig, StreamingMode
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types as genai_types

from backend.agents.real_estate_advisor.agent import app as advisor_app
from backend.agents.real_estate_advisor.subagents.company_knowledge_agent.tools import (
    search_knowledge_base,
)
from backend.agents.real_estate_advisor.subagents.property_database_agent.tools import (
    search_properties,
    get_residency_rules,
)
from backend.src.utils.logger import get_logger

logger = get_logger("services.adk_runner")

APP_NAME = "real_estate_advisor"
ROOT_AGENT_NAME = "real_estate_orchestrator"


def _format_preview(raw: Any) -> Any:
    """Creates a concise, JSON-serializable preview of tool results for the UI."""
    if isinstance(raw, dict):
        preview: Dict[str, Any] = {}
        for k, v in raw.items():
            if k in ("results", "rows", "listings", "lots") and isinstance(v, list):
                preview[k] = [
                    {
                        "title": item.get("title") or item.get("project_name") or item.get("name"),
                        "price": item.get("price_sar") or item.get("pricing") or item.get("price_amount"),
                        "location": item.get("location") or item.get("city"),
                        "url": item.get("listing_url") or item.get("project_url") or item.get("source_url"),
                    }
                    if isinstance(item, dict)
                    else str(item)[:100]
                    for item in v[:3]
                ]
                preview["total_count"] = len(v)
            elif isinstance(v, (str, int, float, bool)) or v is None:
                preview[k] = v
            else:
                preview[k] = str(v)[:200]
        return preview
    elif isinstance(raw, list):
        return [_format_preview(x) for x in raw[:3]]
    return str(raw)[:300]


class ADKRunnerService:
    """Manages sessions and streams events from Google ADK Runner."""

    def __init__(self):
        self.session_service = InMemorySessionService()
        self.runner = Runner(
            app=advisor_app,
            session_service=self.session_service,
            auto_create_session=True,
        )
        logger.info("Initialized ADKRunnerService with app=%s", APP_NAME)

    async def create_session(self, user_id: str = "default_user", session_id: Optional[str] = None) -> str:
        """Explicitly creates a new isolated ADK session."""
        sid = session_id or str(uuid.uuid4())
        session = await self.session_service.create_session(
            app_name=APP_NAME,
            user_id=user_id,
            session_id=sid,
        )
        logger.info("Created ADK session: %s for user: %s", session.id, user_id)
        return session.id

    async def get_session(self, session_id: str, user_id: str = "default_user"):
        """Retrieves session details by ID."""
        return await self.session_service.get_session(
            app_name=APP_NAME,
            user_id=user_id,
            session_id=session_id,
        )

    async def delete_session(self, session_id: str, user_id: str = "default_user") -> bool:
        """Deletes a session and its conversational state."""
        try:
            await self.session_service.delete_session(
                app_name=APP_NAME,
                user_id=user_id,
                session_id=session_id,
            )
            logger.info("Deleted ADK session: %s", session_id)
            return True
        except Exception as exc:
            logger.warning("Error deleting session %s: %s", session_id, exc)
            return False

    async def run_stream(
        self,
        message: str,
        session_id: Optional[str] = None,
        user_id: str = "default_user",
        streaming: bool = True,
    ) -> AsyncGenerator[str, None]:
        """Runs the root agent and yields normalized Server-Sent Events (SSE)."""
        sid = session_id or str(uuid.uuid4())

        # Verify or create session
        existing_session = await self.get_session(sid, user_id)
        if not existing_session:
            sid = await self.create_session(user_id=user_id, session_id=sid)

        # 1. Emit session announcement event
        yield f"data: {json.dumps({'type': 'session', 'session_id': sid})}\n\n"

        # 2. Emit orchestrator activation immediately so UI reflects live activity instantly
        yield f"data: {json.dumps({'type': 'agent_start', 'author': ROOT_AGENT_NAME, 'timestamp': time.time()})}\n\n"

        # Check if live Gemini API key is available
        has_api_key = bool(
            os.getenv("GEMINI_API_KEY")
            or os.getenv("GOOGLE_API_KEY")
            or os.getenv("ADK_API_KEY")
        )

        if has_api_key:
            async for sse_chunk in self._run_live_adk_stream(
                message=message,
                session_id=sid,
                user_id=user_id,
                streaming=streaming,
            ):
                yield sse_chunk
        else:
            logger.warning("No Gemini API key detected in environment. Emitting configuration advisory.")
            yield f"data: {json.dumps({'type': 'error', 'error_code': 'MISSING_API_KEY', 'error_message': 'No GEMINI_API_KEY detected in backend/agents/real_estate_advisor/.env.'})}\n\n"
            yield f"data: {json.dumps({'type': 'final_response', 'author': ROOT_AGENT_NAME, 'text': 'Please ensure your GEMINI_API_KEY is configured in backend/agents/real_estate_advisor/.env to enable live agent inference.', 'total_duration_ms': 0.0})}\n\n"
            yield f"data: {json.dumps({'type': 'done', 'session_id': sid, 'invocation_id': str(uuid.uuid4())})}\n\n"

    async def _run_live_adk_stream(
        self,
        message: str,
        session_id: str,
        user_id: str,
        streaming: bool,
    ) -> AsyncGenerator[str, None]:
        """Streams events directly from google.adk.runners.Runner."""
        user_content = genai_types.Content(
            role="user",
            parts=[genai_types.Part.from_text(text=message)],
        )
        stream_mode = StreamingMode.SSE if streaming else StreamingMode.NONE
        run_config = RunConfig(streaming_mode=stream_mode)

        current_author = ROOT_AGENT_NAME
        tool_start_times: Dict[str, float] = {}
        accumulated_text = ""
        total_start = time.time()
        invocation_id = str(uuid.uuid4())

        try:
            async for event in self.runner.run_async(
                user_id=user_id,
                session_id=session_id,
                new_message=user_content,
                run_config=run_config,
            ):
                if getattr(event, "invocation_id", None):
                    invocation_id = event.invocation_id

                # A. Detect Agent Handoff / Author Change
                event_author = getattr(event, "author", None) or ROOT_AGENT_NAME
                transfer_target = None
                if hasattr(event, "actions") and event.actions:
                    transfer_target = getattr(event.actions, "transfer_to_agent", None)

                target_agent = transfer_target or event_author
                if target_agent and target_agent != current_author:
                    yield f"data: {json.dumps({'type': 'agent_change', 'from_agent': current_author, 'to_agent': target_agent, 'timestamp': time.time()})}\n\n"
                    current_author = target_agent

                # B. Detect Tool Calls
                calls = event.get_function_calls() if hasattr(event, "get_function_calls") else []
                for call in calls:
                    call_id = getattr(call, "id", None) or str(uuid.uuid4())
                    call_name = getattr(call, "name", "unknown_tool")
                    call_args = getattr(call, "args", {}) or {}
                    tool_start_times[call_id] = time.time()
                    tool_start_times[call_name] = time.time()
                    yield f"data: {json.dumps({'type': 'tool_call', 'id': call_id, 'author': current_author, 'name': call_name, 'args': call_args, 'timestamp': time.time()})}\n\n"

                # C. Detect Tool Results
                responses = event.get_function_responses() if hasattr(event, "get_function_responses") else []
                for resp in responses:
                    resp_id = getattr(resp, "id", None) or ""
                    resp_name = getattr(resp, "name", "unknown_tool")
                    start_t = tool_start_times.get(resp_id) or tool_start_times.get(resp_name) or time.time()
                    duration_ms = round((time.time() - start_t) * 1000, 2)
                    raw_resp = getattr(resp, "response", {}) or {}
                    preview = _format_preview(raw_resp)
                    yield f"data: {json.dumps({'type': 'tool_result', 'id': resp_id, 'author': current_author, 'name': resp_name, 'response_preview': preview, 'duration_ms': duration_ms, 'timestamp': time.time()})}\n\n"

                # D. Detect Model Thinking & Text Deltas
                if event.content and hasattr(event.content, "parts"):
                    for part in event.content.parts:
                        is_thought = bool(getattr(part, "thought", False))
                        text = getattr(part, "text", "")
                        if not text:
                            continue

                        if is_thought:
                            yield f"data: {json.dumps({'type': 'thinking', 'author': current_author, 'text': text, 'partial': bool(getattr(event, 'partial', False)), 'timestamp': time.time()})}\n\n"
                        else:
                            if getattr(event, "partial", False):
                                accumulated_text += text
                                yield f"data: {json.dumps({'type': 'text_delta', 'author': current_author, 'text': text, 'partial': True})}\n\n"
                            elif event.is_final_response() or getattr(event, "turn_complete", False):
                                accumulated_text = text

                # E. Final Response
                if event.is_final_response() or getattr(event, "turn_complete", False):
                    full_text = ""
                    if event.content and hasattr(event.content, "parts"):
                        full_text = "".join(
                            getattr(p, "text", "")
                            for p in event.content.parts
                            if not bool(getattr(p, "thought", False))
                        )
                    if not full_text:
                        full_text = accumulated_text

                    total_duration = round((time.time() - total_start) * 1000, 2)
                    usage = getattr(event, "usage_metadata", None)
                    usage_dict = usage.model_dump() if hasattr(usage, "model_dump") else usage
                    yield f"data: {json.dumps({'type': 'final_response', 'author': current_author, 'text': full_text, 'usage_metadata': usage_dict, 'total_duration_ms': total_duration})}\n\n"

                # F. Error Handling
                if getattr(event, "error_code", None) or getattr(event, "error_message", None):
                    yield f"data: {json.dumps({'type': 'error', 'error_code': str(event.error_code), 'error_message': str(event.error_message)})}\n\n"

        except Exception as exc:
            logger.error("Error during live ADK streaming: %s", exc)
            err_msg = str(exc)
            if "429" in err_msg or "quota" in err_msg.lower() or "resource_exhausted" in err_msg.lower():
                user_friendly = "Gemini API rate limit exceeded (429). Please wait a few seconds and try again."
                err_code = "RATE_LIMIT_429"
            else:
                user_friendly = f"Agent communication encountered an error: {err_msg[:120]}"
                err_code = "STREAM_EXCEPTION"

            yield f"data: {json.dumps({'type': 'error', 'error_code': err_code, 'error_message': user_friendly})}\n\n"
            yield f"data: {json.dumps({'type': 'final_response', 'author': ROOT_AGENT_NAME, 'text': user_friendly, 'total_duration_ms': round((time.time() - total_start) * 1000, 2)})}\n\n"

        # G. Done Event
        yield f"data: {json.dumps({'type': 'done', 'session_id': session_id, 'invocation_id': invocation_id})}\n\n"


# Singleton instance for dependency injection
adk_runner_service = ADKRunnerService()
