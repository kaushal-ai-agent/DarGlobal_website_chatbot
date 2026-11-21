"""Unit and integration tests for ADK Session endpoints and SSE Streaming."""

import json
from fastapi.testclient import TestClient
from backend.src.main import app

client = TestClient(app)


def test_session_lifecycle():
    """Verifies creating, inspecting, and deleting isolated ADK sessions."""
    res = client.post("/api/sessions", params={"user_id": "test_agent_user"})
    assert res.status_code == 200
    data = res.json()
    assert "session_id" in data
    sid = data["session_id"]

    inspect_res = client.get(f"/api/sessions/{sid}", params={"user_id": "test_agent_user"})
    assert inspect_res.status_code == 200
    assert inspect_res.json()["session_id"] == sid

    del_res = client.delete(f"/api/sessions/{sid}", params={"user_id": "test_agent_user"})
    assert del_res.status_code == 200
    assert del_res.json()["status"] == "deleted"


def test_sse_streaming_protocol():
    """Verifies that POST /api/chat/stream emits normalized SSE events in real time."""
    req_body = {
        "message": "Tell me about Trump Tower Jeddah",
        "session_id": "test_stream_session_unit",
        "user_id": "test_user",
        "streaming": True,
    }

    observed_types = []
    with client.stream("POST", "/api/chat/stream", json=req_body) as response:
        assert response.status_code == 200
        assert "text/event-stream" in response.headers["content-type"]

        for line in response.iter_lines():
            line_str = line.strip()
            if not line_str or line_str.startswith(":"):
                continue
            if line_str.startswith("data: "):
                payload = json.loads(line_str[6:])
                observed_types.append(payload.get("type"))

    assert "session" in observed_types
    assert ("final_response" in observed_types) or ("error" in observed_types)
    assert "done" in observed_types
