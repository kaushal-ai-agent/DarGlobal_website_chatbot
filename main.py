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
import socket
import sys
import uvicorn
from backend.src.config.settings import settings
from backend.src.main import app

__all__ = ["app"]


def is_port_in_use(port: int, host: str = "127.0.0.1") -> bool:
    """Checks whether the given TCP port is already actively bound."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex((host, port)) == 0


def main() -> None:
    """Run the FastAPI application via Uvicorn with port collision detection."""
    port = int(os.environ.get("PORT", settings.PORT))
    
    if is_port_in_use(port, settings.HOST if settings.HOST != "0.0.0.0" else "127.0.0.1"):
        print(f"\n[ERROR] Port {port} is already in use by another process!")
        print(f"Please terminate any existing uvicorn, main.py, or adk process before starting.")
        print(f"On Windows PowerShell: Stop-Process -Id (Get-NetTCPConnection -LocalPort {port}).OwningProcess -Force\n")
        sys.exit(1)

    uvicorn.run("backend.src.main:app", host=settings.HOST, port=port, reload=settings.DEBUG)


if __name__ == "__main__":
    main()
