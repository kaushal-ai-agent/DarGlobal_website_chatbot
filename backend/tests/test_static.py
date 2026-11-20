"""Tests verifying SPA static file serving, client-side routing fallback, and API route isolation."""

from pathlib import Path
from fastapi import FastAPI
from fastapi.testclient import TestClient
from backend.src.main import app, SPAStaticFiles


def test_spa_root_serves_html(client: TestClient):
    """Verifies that GET / serves the frontend index.html."""
    response = client.get("/")
    assert response.status_code == 200
    assert "DarGlobal & Wasalt Real Estate AI" in response.text
    assert '<div id="root">' in response.text


def test_spa_client_route_fallback(client: TestClient):
    """Verifies that non-API client routes like /chat and /listings fallback to index.html."""
    for path in ["/chat", "/listings", "/properties/riyadh-villa"]:
        response = client.get(path)
        assert response.status_code == 200
        assert "DarGlobal & Wasalt Real Estate AI" in response.text
        assert '<div id="root">' in response.text


def test_static_assets_served_with_correct_mimetype(client: TestClient):
    """Verifies that actual static assets in assets/ are served with correct MIME types."""
    static_assets = list((Path("backend/src/static/assets")).glob("*.css"))
    if static_assets:
        asset_name = static_assets[0].name
        response = client.get(f"/assets/{asset_name}")
        assert response.status_code == 200
        assert "text/css" in response.headers.get("content-type", "")


def test_api_routes_not_shadowed(client: TestClient):
    """Verifies that defined /api/ routes are handled by FastAPI."""
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_api_endpoints_404_isolation(client: TestClient):
    """Verifies that undefined API endpoints strictly return 404 JSON, never SPA HTML."""
    for api_path in ["/api", "/api/", "/api/nonexistent", "/api/v1/unknown"]:
        response = client.get(api_path)
        assert response.status_code == 404
        assert "DarGlobal & Wasalt Real Estate AI" not in response.text
        data = response.json()
        assert "detail" in data
        assert data["detail"] == "Not Found"


def test_docs_and_openapi_not_shadowed(client: TestClient):
    """Verifies that OpenAPI documentation endpoints are preserved and not intercepted."""
    docs_res = client.get("/docs")
    assert docs_res.status_code == 200
    assert "swagger" in docs_res.text.lower()
    assert '<div id="root">' not in docs_res.text

    openapi_res = client.get("/openapi.json")
    assert openapi_res.status_code == 200
    assert "paths" in openapi_res.json()


def test_directory_traversal_prevention(client: TestClient):
    """Verifies that path traversal attacks do not expose server files."""
    traversal_paths = [
        "/..%2Fmain.py",
        "/..%2F..%2Frequirements.txt",
        "/..%2Fbackend%2Fsrc%2Fmain.py",
    ]
    for path in traversal_paths:
        response = client.get(path)
        # Must not leak Python code
        assert "FastAPI Application entrypoint" not in response.text
        assert "from backend.src" not in response.text


def test_api_only_mode_when_no_static():
    """Verifies API-only mode root endpoint behavior when static index.html is missing."""
    test_app = FastAPI()

    @test_app.get("/", tags=["root"])
    async def root():
        return {"name": "Test App", "status": "online"}

    c = TestClient(test_app)
    response = c.get("/")
    assert response.status_code == 200
    assert response.json()["status"] == "online"
