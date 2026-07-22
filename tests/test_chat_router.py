import asyncio
import json
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from api.routers import chat as chat_router
from auth.store import User

U = User(id="u1", handle="alice", role="user")


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
    monkeypatch.setattr(chat_router, "_get_or_create_session", lambda user_id: agent)

    response = asyncio.run(chat_router.chat(chat_router.ChatRequest(message="hi"), user=U))
    assert response.media_type == "text/event-stream"

    chunks = _drain(response)
    assert all(c.startswith("data: ") and c.endswith("\n\n") for c in chunks)
    parsed = [json.loads(c[len("data: "):-2]) for c in chunks]
    assert parsed == events
    assert agent.received == ["hi"]


def test_chat_session_is_keyed_to_the_user(monkeypatch):
    seen = []
    monkeypatch.setattr(chat_router, "_get_or_create_session",
                        lambda user_id: (seen.append(user_id) or _FakeAgent([{"type": "done"}])))
    asyncio.run(chat_router.chat(chat_router.ChatRequest(message="hi"), user=U))
    assert seen == ["u1"]


def test_session_is_created_bound_to_the_authed_user(monkeypatch):
    """The run-creating tool gets the authed user from the per-user agent, not a contextvar
    set around the stream (which does not survive Starlette's threadpool iteration)."""
    made = {}

    class _Stub:
        def __init__(self, agent_id, user_id):
            made["user_id"] = user_id
        def chat_stream(self, message):
            yield {"type": "done"}

    monkeypatch.setattr(chat_router, "MainAgent", _Stub)
    chat_router._sessions.pop("u1", None)
    response = asyncio.run(chat_router.chat(chat_router.ChatRequest(message="hi"), user=U))
    _drain(response)
    assert made["user_id"] == "u1"


def test_chat_session_creation_failure_raises_http_500(monkeypatch):
    def boom(user_id):
        raise RuntimeError("no model configured")

    monkeypatch.setattr(chat_router, "_get_or_create_session", boom)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(chat_router.chat(chat_router.ChatRequest(message="hi"), user=U))
    assert exc.value.status_code == 500


def test_chat_mid_stream_error_is_surfaced_as_sse_error_event(monkeypatch):
    class _BrokenAgent:
        def chat_stream(self, message):
            yield {"type": "token", "content": "partial"}
            raise RuntimeError("boom")

    monkeypatch.setattr(chat_router, "_get_or_create_session", lambda user_id: _BrokenAgent())

    response = asyncio.run(chat_router.chat(chat_router.ChatRequest(message="hi"), user=U))
    chunks = _drain(response)

    parsed = [json.loads(c[len("data: "):-2]) for c in chunks]
    assert parsed[0] == {"type": "token", "content": "partial"}
    assert parsed[1]["type"] == "error"
    assert "boom" in parsed[1]["message"]


def test_clear_session_removes_the_users_own_session():
    chat_router._sessions["u1"] = object()
    result = asyncio.run(chat_router.clear_session(user=U))
    assert result == {"cleared": "u1"}
    assert "u1" not in chat_router._sessions
