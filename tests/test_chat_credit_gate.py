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
from db import store as db_store


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(auth_store, "_db_path", lambda: tmp_path / "auth.db")
    monkeypatch.setattr(db_store, "_db_path", lambda: tmp_path / "platform.db")
    # The turn itself is not under test — only the gate in front of it.
    monkeypatch.setattr(chat_router, "_get_or_create_session",
                        lambda user_id: _StubAgent())
    return TestClient(app)


class _StubAgent:
    def chat_stream(self, message):
        yield {"type": "done", "message": "ok"}


def _user(handle="alice"):
    u = auth_store.create_user(handle, "pw")
    return u, {"Authorization": f"Bearer {auth_store.issue_token(u.id)}"}


def test_a_turn_costs_nothing(client):
    user, headers = _user()
    before = auth_store.balance(user.id)

    r = client.post("/api/chat", headers=headers, json={"message": "make me a game"})
    assert r.status_code == 200
    assert auth_store.balance(user.id) == before


def test_zero_balance_is_refused(client):
    user, headers = _user()
    auth_store.deduct(user.id, auth_store.INITIAL_CREDITS, "drain")

    r = client.post("/api/chat", headers=headers, json={"message": "make me a game"})
    assert r.status_code == 402
    assert r.json()["detail"]["reason"] == "insufficient_credits"
    assert r.json()["detail"]["balance"] == 0


def test_one_credit_is_enough(client):
    """The gate is 'has a balance', not 'can afford a build' — a user mid-conversation must not be
    cut off the moment their last credit is committed to a build."""
    user, headers = _user()
    auth_store.deduct(user.id, auth_store.INITIAL_CREDITS - 1, "drain")
    assert auth_store.balance(user.id) == 1

    assert client.post("/api/chat", headers=headers,
                       json={"message": "hi"}).status_code == 200


def test_clearing_a_session_is_not_gated(client):
    """Housekeeping must stay reachable at a zero balance — otherwise a broke user is stuck with
    whatever context their session already holds."""
    user, headers = _user()
    auth_store.deduct(user.id, auth_store.INITIAL_CREDITS, "drain")

    assert client.delete("/api/chat", headers=headers).status_code == 200


def test_the_gate_still_requires_a_token(client):
    assert client.post("/api/chat", json={"message": "hi"}).status_code == 401
