"""Health check route for container orchestrators and monitoring."""

from fastapi import APIRouter
from backend.src.config.settings import settings
from backend.src.models.schemas import HealthResponse

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
async def check_health() -> HealthResponse:
    """Returns the operational status and environment of the service."""
    return HealthResponse(
        status="ok",
        version="0.1.0",
        environment=settings.APP_ENV,
    )
