"""The auth gate as it actually runs on the app: every router rejects an anonymous request,
a valid token gets through, the WebSocket authenticates itself, and there is no signup route."""

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
    from api.routers import games
    monkeypatch.setattr(games, "_runs_dir", lambda: tmp_path / "runs")
    # No `with` — we don't want the app's startup handlers (tool registration) in a unit test.
    return TestClient(app)


# One path per mounted router; the gate is a single app-level middleware, so covering the
# routers proves the whole surface.
GATED_PATHS = [
    "/api/games",
    "/api/agents/",
    "/api/system/status",
    "/api/outputs/whatever.txt",
    "/api/chat",
]


@pytest.mark.parametrize("path", GATED_PATHS)
def test_anonymous_request_is_rejected(app_client, path):
    assert app_client.get(path).status_code == 401


def test_health_and_login_are_public(app_client):
    assert app_client.get("/").status_code == 200
    # login reached without a token (422 = body validation, i.e. it got PAST the gate).
    assert app_client.post("/auth/login").status_code == 422


def test_valid_token_gets_through_the_gate(app_client):
    store.create_user("alice", "pw")
    token = store.issue_token(store.get_user_by_handle("alice").id)
    r = app_client.get("/api/games", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert r.json() == []


def test_bad_token_is_rejected(app_client):
    r = app_client.get("/api/games", headers={"Authorization": "Bearer nonsense"})
    assert r.status_code == 401


def test_no_signup_route_exists_on_the_app(app_client):
    paths = {getattr(r, "path", "") for r in app_client.app.routes}
    assert "/auth/login" in paths
    assert not any("signup" in p or "register" in p for p in paths)


def test_websocket_requires_a_token(app_client):
    from starlette.websockets import WebSocketDisconnect
    with pytest.raises(WebSocketDisconnect):
        with app_client.websocket_connect("/api/ws"):
            pass


def test_websocket_accepts_a_valid_token(app_client):
    store.create_user("alice", "pw")
    token = store.issue_token(store.get_user_by_handle("alice").id)
    with app_client.websocket_connect(f"/api/ws?token={token}") as ws:
        assert ws.receive_json()["type"] == "connected"
