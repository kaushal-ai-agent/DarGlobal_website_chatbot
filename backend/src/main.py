"""FastAPI Application entrypoint with API routes and SPA StaticFiles mounting."""

import os
import sys
from pathlib import Path
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import Scope

# Ensure project root is in sys.path when running backend/src/main.py directly
PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.src.config.settings import settings
from backend.src.routes.api import api_router
from backend.src.utils.logger import get_logger

logger = get_logger("main")


class SPAStaticFiles(StaticFiles):
    """Production-grade StaticFiles handler serving compiled Vite assets and falling

    back to index.html for client-side SPA routing, while enforcing strict API/docs
    isolation and preventing directory traversal leaks.
    """

    async def get_response(self, path: str, scope: Scope):
        # Normalize Windows backslashes to forward slashes and strip leading/trailing slashes
        clean_path = path.replace("\\", "/").strip("/")

        # Strict isolation: Never intercept API, OpenAPI documentation, or schema endpoints
        if (
            clean_path == "api"
            or clean_path.startswith("api/")
            or clean_path in ("docs", "redoc", "openapi.json")
            or clean_path.startswith(("docs/", "redoc/"))
        ):
            raise HTTPException(status_code=404, detail="Not Found")

        try:
            response = await super().get_response(path, scope)
            # Fallback to index.html for SPA client-side routes (e.g., /chat, /listings)
            if response.status_code == 404:
                return await super().get_response("index.html", scope)
            return response
        except (HTTPException, StarletteHTTPException) as exc:
            if exc.status_code == 404:
                return await super().get_response("index.html", scope)
            raise exc


app = FastAPI(
    title=settings.APP_NAME,
    version="0.1.0",
    description="Production-grade API and static asset host for DarGlobal & Wasalt Real Estate AI Chatbot.",
)

# CORS configuration for development and multi-host access
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register all modular API endpoints under /api
app.include_router(api_router, prefix="/api")

# Determine static frontend directory (compiled Vite dist)
STATIC_DIR = Path(__file__).resolve().parent / "static"

if STATIC_DIR.is_dir() and (STATIC_DIR / "index.html").is_file():
    logger.info("Mounting production static assets from: %s", STATIC_DIR)
    # Mount SPA static files at root
    app.mount("/", SPAStaticFiles(directory=str(STATIC_DIR), html=True), name="static-spa")
else:
    logger.warning("No static index.html found at %s. Running in API-only mode.", STATIC_DIR)

    @app.get("/", tags=["root"])
    async def root():
        """Root status route when running in API-only mode."""
        return {
            "name": settings.APP_NAME,
            "status": "online",
            "environment": settings.APP_ENV,
            "docs": "/docs",
            "health": "/api/health",
        }


if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("PORT", settings.PORT))
    uvicorn.run("backend.src.main:app", host=settings.HOST, port=port, reload=settings.DEBUG)
