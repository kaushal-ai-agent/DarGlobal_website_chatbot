"""Chat endpoint interfacing frontend queries with the Agent service."""

from fastapi import APIRouter, Depends
from backend.src.models.schemas import ChatRequest, ChatResponse
from backend.src.services.agent import AgentService

router = APIRouter(tags=["chat"])


def get_agent_service() -> AgentService:
    """Dependency provider for AgentService."""
    return AgentService()


@router.post("/chat", response_model=ChatResponse)
async def chat_endpoint(
    request: ChatRequest,
    agent_service: AgentService = Depends(get_agent_service),
) -> ChatResponse:
    """Receives user query, retrieves properties via RAG, and produces assistant response."""
    return await agent_service.process_chat(request)
