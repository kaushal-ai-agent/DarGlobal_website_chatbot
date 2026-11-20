"""Application configuration management using Pydantic Settings."""

import os
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field


class Settings(BaseSettings):
    """Central application settings with environment variable fallbacks."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    APP_NAME: str = "DarGlobal & Wasalt Real Estate AI Chatbot"
    APP_ENV: str = Field(default="development", description="development | staging | production")
    DEBUG: bool = Field(default=True, description="Enable debug mode")
    PORT: int = Field(default=8000, description="Server port")
    HOST: str = Field(default="0.0.0.0", description="Server host interface")

    # OpenRouter / LiteLLM Settings
    OPENROUTER_API_KEY: str = Field(default="", description="OpenRouter API Key")
    DEFAULT_LLM_MODEL: str = Field(
        default="meta-llama/llama-3-8b-instruct:free",
        description="Target model on OpenRouter",
    )

    # Local ONNX Embedding & Vector Store Settings
    EMBEDDING_MODEL_NAME: str = Field(
        default="all-MiniLM-L6-v2",
        description="Local HuggingFace embedding model name (kept for backwards compatibility)",
    )
    CHROMA_PERSIST_DIRECTORY: str = Field(
        default="./data/chroma_db",
        description="Local directory for legacy ChromaDB storage",
    )
    ONNX_EMBED_MODEL: str = Field(
        default="BAAI/bge-small-en-v1.5",
        description="Local FastEmbed ONNX embedding model",
    )
    ONNX_RERANK_MODEL: str = Field(
        default="ms-marco-MiniLM-L-12-v2",
        description="Local FlashRank ONNX cross-encoder reranking model",
    )
    QDRANT_PATH: str = Field(
        default="./data/index/qdrant",
        description="Local embedded Qdrant vector database path",
    )
    FTS5_DB_PATH: str = Field(
        default="./data/index/rag_index.db",
        description="Local SQLite FTS5 sparse search database path",
    )
    REAL_ESTATE_DB_PATH: str = Field(
        default="./data/structured/db/real_estate.db",
        description="Normalized SQLite structured database path",
    )

    # Scraping Configuration
    SCRAPE_HEADLESS: bool = Field(default=True, description="Run Playwright in headless mode")
    SCRAPE_CONCURRENCY: int = Field(default=3, description="Concurrent browser pages limit")


settings = Settings()
