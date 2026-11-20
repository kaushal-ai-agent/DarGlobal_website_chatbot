"""Tests for application settings and defaults."""

from backend.src.config.settings import Settings


def test_settings_defaults():
    """Verifies default configuration properties."""
    s = Settings()
    assert s.APP_NAME == "DarGlobal & Wasalt Real Estate AI Chatbot"
    assert s.PORT == 8000
    assert s.DEFAULT_LLM_MODEL == "meta-llama/llama-3-8b-instruct:free"
    assert s.EMBEDDING_MODEL_NAME == "all-MiniLM-L6-v2"
