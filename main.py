"""
Root Application Entrypoint (`main.py`)

What it does:
Serves as the root launcher for the DarGlobal & Wasalt Real Estate AI Chatbot platform, starting the FastAPI application through Uvicorn with environment-configured host and port bindings.

Important Modules & Functions:
- `uvicorn`: ASGI server runner hosting the FastAPI web application.
- `backend.src.config.settings.settings`: Central application settings (host, port, debug mode, environment).
- `backend.src.main.app`: Main FastAPI application instance with mounted API routers and SPA static handlers.
- `main()`: Resolves port from environment or settings and triggers `uvicorn.run`.

Core Logic (One-Liner):
Boots the FastAPI ASGI backend server via Uvicorn with dynamic port binding and hot-reload support.
"""

import os
import uvicorn
from backend.src.config.settings import settings
from backend.src.main import app

__all__ = ["app"]


def main() -> None:
    """Run the FastAPI application via Uvicorn."""
    port = int(os.environ.get("PORT", settings.PORT))
    uvicorn.run("backend.src.main:app", host=settings.HOST, port=port, reload=settings.DEBUG)


if __name__ == "__main__":
    main()
