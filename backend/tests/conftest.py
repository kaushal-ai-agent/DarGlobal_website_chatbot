"""Pytest test fixtures."""

import pytest
from fastapi.testclient import TestClient
from backend.src.main import app


@pytest.fixture
def client() -> TestClient:
    """Provides a FastAPI test client instance."""
    return TestClient(app)
