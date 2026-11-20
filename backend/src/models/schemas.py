"""Pydantic V2 data contracts for API, scraping, and RAG entities."""

from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional
from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    """Health check response payload."""
    status: Literal["ok", "degraded", "down"] = "ok"
    version: str = "0.1.0"
    environment: str = "development"
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class PropertyItem(BaseModel):
    """Structured property listing extracted from DarGlobal or Wasalt."""
    id: str = Field(..., description="Unique property identifier or slug")
    source: Literal["darglobal", "wasalt"] = Field(..., description="Origin portal")
    title: str = Field(..., description="Property name / headline")
    location: str = Field(..., description="City, district, or development area")
    price_formatted: Optional[str] = Field(None, description="Display price string (e.g. SAR 2,500,000)")
    price_numerical: Optional[float] = Field(None, description="Standardized numerical price")
    currency: Optional[str] = Field("SAR", description="ISO currency code")
    property_type: Optional[str] = Field(None, description="Villa, Apartment, Penthouse, etc.")
    bedrooms: Optional[int] = Field(None, description="Number of bedrooms")
    bathrooms: Optional[int] = Field(None, description="Number of bathrooms")
    area_sqft: Optional[float] = Field(None, description="Total built-up area in square feet")
    amenities: List[str] = Field(default_factory=list, description="Key amenities listed")
    description: Optional[str] = Field(None, description="Detailed text description")
    status: Optional[str] = Field(None, description="Development status (e.g. Under Development, Ready, Sold Out)")
    raw_text: Optional[str] = Field(None, description="Full unstructured text extracted from the page (contacts, FAQs, details)")
    contact_info: Optional[Dict[str, Any]] = Field(default_factory=dict, description="Extracted contact phone numbers, emails, addresses")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Additional unstructured and portal metadata")
    source_url: str = Field(..., description="Direct listing URL")
    image_url: Optional[str] = Field(None, description="Primary showcase image URL")


class ChatMessage(BaseModel):
    """Single turn in a chat dialogue."""
    role: Literal["user", "assistant", "system"]
    content: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ChatRequest(BaseModel):
    """Incoming user chat query."""
    message: str = Field(..., min_length=1, description="User prompt or inquiry")
    conversation_id: Optional[str] = Field(None, description="Session ID for multi-turn history")
    history: List[ChatMessage] = Field(default_factory=list, description="Recent conversation history")
    filters: Optional[Dict[str, Any]] = Field(default=None, description="Optional metadata filters (location, price)")


class ChatResponse(BaseModel):
    """Outgoing assistant response with grounded property citations."""
    reply: str = Field(..., description="Generated answer from the agentic RAG pipeline")
    conversation_id: str = Field(..., description="Session identifier")
    matched_properties: List[PropertyItem] = Field(
        default_factory=list,
        description="Retrieved property cards supporting the answer",
    )
    tokens_used: Optional[int] = Field(None, description="Total prompt + completion tokens")
