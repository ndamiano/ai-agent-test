"""Builds and note-fixes share one queue: a fix is visible in the run's status, and neither kind
can start while the other holds the run. Regression for a fix that ran off-queue — invisible to
GET /games/{id} (which reported "built"), so the client's next fix hit a bare 409."""

import sys
import threading
from pathlib import Path
from unittest import mock

import pytest
from starlette.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import maestro.state
from api import build_queue as bq_mod
from api.app import app
from api.routers import games as games_router
from auth import store as auth_store
from auth.billing import SECONDS_PER_CREDIT
from db import store as db_store
from maestro.codegen.run import create_run
from maestro.state import RunState


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(auth_store, "_db_path", lambda: tmp_path / "auth.db")
    monkeypatch.setattr(db_store, "_db_path", lambda: tmp_path / "platform.db")
    monkeypatch.setattr(maestro.state, "resolve_base_path", lambda input_path=None: tmp_path)
    queue = bq_mod.build_queue
    yield TestClient(app)   # no lifespan → no drain thread: enqueued jobs just park
    with queue._lock:
        queue._pending.clear()
        queue._queued_ids.clear()
        queue._current = None
        queue._current_kind = None


def _user(handle="alice"):
    u = auth_store.create_user(handle, "pw")
    return u, {"Authorization": f"Bearer {auth_store.issue_token(u.id)}"}


def _claim(queue):
    """What the drain thread does when it picks the head item up (no worker runs in tests)."""
    with queue._lock:
        item = queue._pending.popleft()
        queue._queued_ids.discard(item.run_id)
        queue._current, queue._current_kind = item.run_id, item.kind
    return item


def _make_game(user_id, frozen=True):
    run_id = create_run(user_id)
    spec = {"title": "Moon Miner", "mode": "2d", "frozen": frozen}
    RunState(run_id).write_spec(spec)
    db_store.update_spec_meta(run_id, spec["title"], spec["mode"], frozen)
    # Charged, as any game that has reached fix/assets is: those endpoints refuse a run with no
    # compute left, and an ungranted game has none.
    db_store.charge_game(run_id, 1, SECONDS_PER_CREDIT)
    return run_id


def test_fix_rides_the_build_queue_and_shows_as_fixing(client):
    user, headers = _user()
    run_id = _make_game(user.id)

    r = client.post(f"/api/games/{run_id}/fix", headers=headers, json={"note": "player falls"})
    assert r.status_code == 200
    assert r.json()["status"] == "fixing"
    assert bq_mod.build_queue.state_of(run_id)["kind"] == "fix"
    (build,) = db_store.builds_for(run_id)
    assert build["kind"] == "fix"

    _claim(bq_mod.build_queue)
    detail = client.get(f"/api/games/{run_id}", headers=headers).json()
    assert (detail["status"], detail["building"]) == ("fixing", True)


def test_second_fix_while_one_is_active_is_409(client):
    user, headers = _user()
    run_id = _make_game(user.id)

    assert client.post(f"/api/games/{run_id}/fix", headers=headers,
                       json={"note": "a"}).status_code == 200
    r = client.post(f"/api/games/{run_id}/fix", headers=headers, json={"note": "b"})
    assert r.status_code == 409
    assert "already running" in r.json()["detail"]


def test_build_and_fix_cannot_hold_the_same_run_at_once(client):
    user, headers = _user()
    run_id = _make_game(user.id)

    assert client.post(f"/api/games/{run_id}/build", headers=headers).status_code == 200
    r = client.post(f"/api/games/{run_id}/fix", headers=headers, json={"note": "a"})
    assert r.status_code == 409

    other = _make_game(user.id)
    assert client.post(f"/api/games/{other}/fix", headers=headers,
                       json={"note": "a"}).status_code == 200
    assert client.post(f"/api/games/{other}/build", headers=headers).status_code == 409


def test_a_fix_queued_behind_a_build_reports_its_position(client):
    user, headers = _user()
    first, second = _make_game(user.id), _make_game(user.id)

    client.post(f"/api/games/{first}/build", headers=headers)
    r = client.post(f"/api/games/{second}/fix", headers=headers, json={"note": "a"})
    assert (r.json()["status"], r.json()["queue_position"]) == ("queued", 1)

    detail = client.get(f"/api/games/{second}", headers=headers).json()
    assert (detail["status"], detail["queue_position"]) == ("queued", 1)


def test_a_failed_asset_start_releases_its_slot(client, monkeypatch):
    """The skin guard is an in-memory key claimed before the thread exists — a throw in between
    used to strand it, 409ing every later skin of that run until the process restarted."""
    user, headers = _user()
    run_id = _make_game(user.id)

    def _boom(*a, **kw):
        raise RuntimeError("db down")

    with mock.patch.object(games_router.db_store, "create_build", _boom):
        with pytest.raises(RuntimeError):
            client.post(f"/api/games/{run_id}/assets", headers=headers)
    assert f"assets:{run_id}" not in games_router._active

    monkeypatch.setattr(games_router, "add_assets", lambda rid: {"ok": True, "generated": []})
    assert client.post(f"/api/games/{run_id}/assets", headers=headers).status_code == 200
    for t in threading.enumerate():
        if t.name == f"assets-{run_id}":
            t.join(timeout=5)
