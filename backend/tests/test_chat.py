"""Tests for chat endpoint contracts, multi-turn history, filters, and edge cases."""

from fastapi.testclient import TestClient


def test_chat_endpoint_valid_request(client: TestClient):
    """Verifies that POST /api/chat returns a structured reply with matching session ID."""
    payload = {
        "message": "Tell me about luxury villas in Riyadh by DarGlobal",
        "conversation_id": "test-session-123",
    }
    response = client.post("/api/chat", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert "reply" in data
    assert data["conversation_id"] == "test-session-123"
    assert isinstance(data["matched_properties"], list)


def test_chat_endpoint_generates_session_id_when_omitted(client: TestClient):
    """Verifies that conversation_id is auto-generated when not provided by client."""
    payload = {"message": "Looking for 4 bedroom villas in Dubai"}
    response = client.post("/api/chat", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert "conversation_id" in data
    assert len(data["conversation_id"]) > 0


def test_chat_endpoint_empty_message_validation(client: TestClient):
    """Verifies that empty message strings trigger 422 Unprocessable Entity."""
    payload = {"message": ""}
    response = client.post("/api/chat", json=payload)
    assert response.status_code == 422


def test_chat_endpoint_with_history_and_filters(client: TestClient):
    """Verifies chat turn with conversation history and structured metadata filters."""
    payload = {
        "message": "What is the price range for those apartments?",
        "conversation_id": "session-456",
        "history": [
            {"role": "user", "content": "Show apartments in Jeddah"},
            {"role": "assistant", "content": "Here are 3 listings in Jeddah"},
        ],
        "filters": {
            "city": "Jeddah",
            "bedrooms": 2,
            "max_price": 3000000,
        },
    }
    response = client.post("/api/chat", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["conversation_id"] == "session-456"
    assert "reply" in data


def test_chat_endpoint_arabic_unicode_query(client: TestClient):
    """Verifies that Arabic / Unicode queries are accepted and processed properly."""
    payload = {
        "message": "ابحث عن فلل فاخرة للبيع في الرياض بمساحة 500 متر مربع",
    }
    response = client.post("/api/chat", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert "reply" in data
