"""Tests for Pydantic V2 domain models and schema validations."""

import pytest
from pydantic import ValidationError
from backend.src.models.schemas import (
    HealthResponse,
    PropertyItem,
    ChatMessage,
    ChatRequest,
    ChatResponse,
)


def test_property_item_valid():
    """Verifies that a complete property listing parses correctly."""
    item = PropertyItem(
        id="dg-riyadh-01",
        source="darglobal",
        title="Les Vagues Luxury Villa",
        location="Riyadh, Saudi Arabia",
        price_formatted="SAR 5,200,000",
        price_numerical=5200000.0,
        currency="SAR",
        property_type="Villa",
        bedrooms=5,
        bathrooms=6,
        area_sqft=6500.0,
        amenities=["Private Pool", "Smart Home", "Garden"],
        source_url="https://darglobal.co.uk/project/les-vagues",
    )
    assert item.id == "dg-riyadh-01"
    assert item.source == "darglobal"
    assert item.bedrooms == 5
    assert len(item.amenities) == 3


def test_property_item_invalid_source():
    """Verifies that invalid source strings fail validation."""
    with pytest.raises(ValidationError):
        PropertyItem(
            id="test-01",
            source="unsupported_source",  # only 'darglobal' or 'wasalt' allowed
            title="Test Property",
            location="Dubai",
            source_url="https://example.com",
        )


def test_chat_message_role_validation():
    """Verifies that role must be user, assistant, or system."""
    msg = ChatMessage(role="user", content="Hello")
    assert msg.role == "user"

    with pytest.raises(ValidationError):
        ChatMessage(role="invalid_role", content="Hello")


def test_health_response_defaults():
    """Verifies HealthResponse default values and timestamp generation."""
    hr = HealthResponse()
    assert hr.status == "ok"
    assert hr.version == "0.1.0"
    assert hr.timestamp is not None
