"""Master API router aggregating modular sub-routers."""

from fastapi import APIRouter
from backend.src.routes.health import router as health_router
from backend.src.routes.chat import router as chat_router

api_router = APIRouter()

# Register modular sub-routes
api_router.include_router(health_router)
api_router.include_router(chat_router)
