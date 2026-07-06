import asyncio
import json
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from api.routers import chat as chat_router


class _FakeAgent:
    def __init__(self, events):
        self._events = events
        self.received = []

    def chat_stream(self, message):
        self.received.append(message)
        yield from self._events


def _drain(response):
    async def _collect():
        chunks = []
        async for chunk in response.body_iterator:
            chunks.append(chunk)
        return chunks
    return asyncio.run(_collect())


def test_chat_streams_sse_events_in_order(monkeypatch):
    events = [
        {"type": "token", "content": "Hello"},
        {"type": "tool", "tool_name": "propose_game_spec", "status": "start"},
        {"type": "tool", "tool_name": "propose_game_spec", "status": "success"},
        {"type": "done", "message": "Hello"},
    ]
    agent = _FakeAgent(events)
    monkeypatch.setattr(chat_router, "_get_or_create_session", lambda session_id: agent)

    response = asyncio.run(chat_router.chat(chat_router.ChatRequest(message="hi", session_id="s1")))
    assert response.media_type == "text/event-stream"

    chunks = _drain(response)
    assert all(c.startswith("data: ") and c.endswith("\n\n") for c in chunks)
    parsed = [json.loads(c[len("data: "):-2]) for c in chunks]
    assert parsed == events
    assert agent.received == ["hi"]


def test_chat_session_creation_failure_raises_http_500(monkeypatch):
    def boom(session_id):
        raise RuntimeError("no model configured")

    monkeypatch.setattr(chat_router, "_get_or_create_session", boom)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(chat_router.chat(chat_router.ChatRequest(message="hi")))
    assert exc.value.status_code == 500


def test_chat_mid_stream_error_is_surfaced_as_sse_error_event(monkeypatch):
    class _BrokenAgent:
        def chat_stream(self, message):
            yield {"type": "token", "content": "partial"}
            raise RuntimeError("boom")

    monkeypatch.setattr(chat_router, "_get_or_create_session", lambda session_id: _BrokenAgent())

    response = asyncio.run(chat_router.chat(chat_router.ChatRequest(message="hi")))
    chunks = _drain(response)

    parsed = [json.loads(c[len("data: "):-2]) for c in chunks]
    assert parsed[0] == {"type": "token", "content": "partial"}
    assert parsed[1]["type"] == "error"
    assert "boom" in parsed[1]["message"]


def test_clear_session_removes_session():
    chat_router._sessions["to-clear"] = object()
    result = asyncio.run(chat_router.clear_session("to-clear"))
    assert result == {"cleared": "to-clear"}
    assert "to-clear" not in chat_router._sessions
