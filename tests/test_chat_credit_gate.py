"""Chat is free but not open: a turn deducts no credits and meters no seconds, yet it requires a
balance to spend. Drafting a spec the user could never afford to build is pure cost."""

import sys
from pathlib import Path

import pytest
from starlette.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from api.app import app
from api.routers import chat as chat_router
from auth import store as auth_store
from auth.ratelimit import RequestThrottle
from db import store as db_store


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(auth_store, "_db_path", lambda: tmp_path / "auth.db")
    monkeypatch.setattr(db_store, "_db_path", lambda: tmp_path / "platform.db")
    # The turn itself is not under test — only the gates in front of it. A fresh throttle per
    # test keeps the module-global's state from leaking across tests.
    monkeypatch.setattr(chat_router, "_get_or_create_session",
                        lambda user_id: _StubAgent())
    monkeypatch.setattr(chat_router, "chat_throttle", RequestThrottle(3600, 30))
    return TestClient(app)


class _StubAgent:
    def chat_stream(self, message):
        yield {"type": "done", "message": "ok"}


def _user(handle="alice", credits=0):
    u = auth_store.create_user(handle, "pw")
    if credits:
        auth_store.grant(u.id, credits, "admin_grant")
    return u, {"Authorization": f"Bearer {auth_store.issue_token(u.id)}"}


def test_a_turn_costs_nothing(client):
    user, headers = _user(credits=5)

    r = client.post("/api/chat", headers=headers, json={"message": "make me a game"})
    assert r.status_code == 200
    assert auth_store.balance(user.id) == 5


def test_zero_balance_is_refused(client):
    # Accounts start broke — no grant, no chat.
    user, headers = _user()

    r = client.post("/api/chat", headers=headers, json={"message": "make me a game"})
    assert r.status_code == 402
    assert r.json()["detail"]["reason"] == "insufficient_credits"
    assert r.json()["detail"]["balance"] == 0


def test_one_credit_is_enough(client):
    """The gate is 'has a balance', not 'can afford a build' — a user mid-conversation must not be
    cut off the moment their last credit is committed to a build."""
    user, headers = _user(credits=1)

    assert client.post("/api/chat", headers=headers,
                       json={"message": "hi"}).status_code == 200


def test_clearing_a_session_is_not_gated(client):
    """Housekeeping must stay reachable at a zero balance — otherwise a broke user is stuck with
    whatever context their session already holds."""
    user, headers = _user()

    assert client.delete("/api/chat", headers=headers).status_code == 200


def test_the_gate_still_requires_a_token(client):
    assert client.post("/api/chat", json={"message": "hi"}).status_code == 401


def test_chat_turns_are_rate_limited_per_user(client, monkeypatch):
    """Chat is uncharged inference — without a cap one funded account could loop turns and burn
    llm-worker GPU at zero marginal cost. Over the cap → 429 with Retry-After; other users are
    unaffected."""
    monkeypatch.setattr(chat_router, "chat_throttle", RequestThrottle(3600, 2))
    user, headers = _user(credits=5)

    assert client.post("/api/chat", headers=headers, json={"message": "a"}).status_code == 200
    assert client.post("/api/chat", headers=headers, json={"message": "b"}).status_code == 200
    r = client.post("/api/chat", headers=headers, json={"message": "c"})
    assert r.status_code == 429
    assert "retry-after" in {k.lower() for k in r.headers}

    _, other_headers = _user("bob", credits=5)
    assert client.post("/api/chat", headers=other_headers,
                       json={"message": "hi"}).status_code == 200
