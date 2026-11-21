"""Chat and ADK Session endpoints interfacing client interactions with Google ADK agents."""

from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from backend.src.models.schemas import (
    ChatRequest,
    ChatResponse,
    SessionDetailResponse,
    SessionResponse,
    StreamChatRequest,
)
from backend.src.services.adk_runner import ADKRunnerService, adk_runner_service
from backend.src.services.agent import AgentService
from backend.src.utils.logger import get_logger

logger = get_logger("routes.chat")

router = APIRouter(tags=["chat"])


def get_agent_service() -> AgentService:
    """Dependency provider for non-streaming AgentService."""
    return AgentService()


def get_adk_runner_service() -> ADKRunnerService:
    """Dependency provider for ADKRunnerService."""
    return adk_runner_service


# ------------------------------------------------------------------------------
# ADK Session Management Routes
# ------------------------------------------------------------------------------
@router.post("/sessions", response_model=SessionResponse)
async def create_new_session(
    user_id: str = "default_user",
    adk_service: ADKRunnerService = Depends(get_adk_runner_service),
) -> SessionResponse:
    """Creates a new isolated Google ADK conversation session."""
    session_id = await adk_service.create_session(user_id=user_id)
    return SessionResponse(
        session_id=session_id,
        app_name="real_estate_advisor",
        user_id=user_id,
    )


@router.get("/sessions/{session_id}", response_model=SessionDetailResponse)
async def get_session_info(
    session_id: str,
    user_id: str = "default_user",
    adk_service: ADKRunnerService = Depends(get_adk_runner_service),
) -> SessionDetailResponse:
    """Retrieves session status and event count."""
    session = await adk_service.get_session(session_id=session_id, user_id=user_id)
    if not session:
        raise HTTPException(status_code=404, detail=f"Session '{session_id}' not found.")
    
    events = getattr(session, "events", []) or []
    return SessionDetailResponse(
        session_id=session.id,
        app_name="real_estate_advisor",
        user_id=user_id,
        event_count=len(events),
    )


@router.delete("/sessions/{session_id}")
async def delete_session_endpoint(
    session_id: str,
    user_id: str = "default_user",
    adk_service: ADKRunnerService = Depends(get_adk_runner_service),
):
    """Deletes an active session and purges in-memory event state."""
    success = await adk_service.delete_session(session_id=session_id, user_id=user_id)
    if not success:
        raise HTTPException(status_code=404, detail=f"Session '{session_id}' could not be deleted.")
    return {"status": "deleted", "session_id": session_id}


# ------------------------------------------------------------------------------
# Streaming & Non-Streaming Chat Execution Routes
# ------------------------------------------------------------------------------
@router.post("/chat/stream")
async def stream_chat_endpoint(
    request: StreamChatRequest,
    adk_service: ADKRunnerService = Depends(get_adk_runner_service),
):
    """Streams real-time ADK events, thinking tokens, and tool calls over Server-Sent Events (SSE)."""
    logger.info("Starting SSE stream for session=%s query='%s'", request.session_id, request.message[:60])
    
    generator = adk_service.run_stream(
        message=request.message,
        session_id=request.session_id,
        user_id=request.user_id,
        streaming=request.streaming,
    )

    headers = {
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",  # Disables proxy buffering for instant delivery on Render/Nginx
    }
    return StreamingResponse(generator, media_type="text/event-stream", headers=headers)


@router.post("/chat", response_model=ChatResponse)
async def chat_endpoint(
    request: ChatRequest,
    agent_service: AgentService = Depends(get_agent_service),
) -> ChatResponse:
    """Receives user query, executes agentic reasoning, and produces structured assistant response."""
    return await agent_service.process_chat(request)
