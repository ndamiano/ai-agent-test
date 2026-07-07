"""GET /auth/me — the balance source the frontend header reads. Anonymous is rejected by the
gate; a valid token returns the user plus their live credit balance."""

import sys
from pathlib import Path

import pytest
from starlette.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from auth import store


@pytest.fixture
def app_client(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "_db_path", lambda: tmp_path / "auth.db")
    from api.app import app
    return TestClient(app)


def test_me_rejects_anonymous(app_client):
    assert app_client.get("/auth/me").status_code == 401


def test_me_returns_the_balance_for_a_valid_token(app_client):
    store.create_user("alice", "pw")
    uid = store.get_user_by_handle("alice").id
    token = store.issue_token(uid)

    r = app_client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    body = r.json()
    assert body["handle"] == "alice"
    assert body["balance"] == store.INITIAL_CREDITS

    store.deduct(uid, 3, "test")
    r2 = app_client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert r2.json()["balance"] == store.INITIAL_CREDITS - 3


def test_me_accepts_a_token_query_param(app_client):
    """The <img>/download channel: a token in the query string authenticates too."""
    store.create_user("bob", "pw")
    token = store.issue_token(store.get_user_by_handle("bob").id)
    r = app_client.get(f"/auth/me?token={token}")
    assert r.status_code == 200
    assert r.json()["handle"] == "bob"
