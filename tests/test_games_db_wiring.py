"""The games API against the platform db: list/detail read db rows, ownership gates cross-user
access, and the build endpoint charges once — credits deducted, seconds granted, re-enqueue free."""

import sys
from pathlib import Path

import pytest
from starlette.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import maestro.state
from api import build_queue as bq_mod
from api.app import app
from auth import store as auth_store
from auth.billing import SECONDS_PER_CREDIT
from db import store as db_store
from db.estimates import estimate_seconds
from maestro.codegen.run import create_run
from maestro.state import RunState


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(auth_store, "_db_path", lambda: tmp_path / "auth.db")
    monkeypatch.setattr(db_store, "_db_path", lambda: tmp_path / "platform.db")
    monkeypatch.setattr(maestro.state, "resolve_base_path", lambda input_path=None: tmp_path)
    return TestClient(app)


def _user(handle="alice"):
    u = auth_store.create_user(handle, "pw")
    return u, {"Authorization": f"Bearer {auth_store.issue_token(u.id)}"}


def _make_game(user_id, spec):
    run_id = create_run(user_id)
    RunState(run_id).write_spec(spec)
    db_store.update_spec_meta(run_id, spec.get("title", ""), spec.get("mode", ""),
                              bool(spec.get("frozen")))
    return run_id


def test_list_and_detail_come_from_the_db(client):
    user, headers = _user()
    run_id = _make_game(user.id, {"title": "Moon Miner", "mode": "2d", "frozen": False})

    (row,) = client.get("/api/games", headers=headers).json()
    assert (row["run_id"], row["title"], row["frozen"]) == (run_id, "Moon Miner", False)

    detail = client.get(f"/api/games/{run_id}", headers=headers).json()
    assert detail["spec"]["title"] == "Moon Miner"
    assert detail["credits_spent"] == 0
    assert detail["seconds_granted"] == 0


def test_cross_user_access_is_403(client):
    user, _ = _user("alice")
    _, other_headers = _user("bob")
    run_id = _make_game(user.id, {"title": "Mine", "mode": "2d", "frozen": False})
    assert client.get(f"/api/games/{run_id}", headers=other_headers).status_code == 403
    assert client.get("/api/games", headers=other_headers).json() == []


def test_build_charges_once_and_grants_seconds(client, monkeypatch):

    user, headers = _user()
    run_id = _make_game(user.id, {"title": "Moon Miner", "mode": "2d", "frozen": True})

    # Enqueue without running a real build (no worker thread in tests).
    monkeypatch.setattr(bq_mod.build_queue, "enqueue",
                        lambda rid, uid, auto_pause=False: 0)

    r = client.post(f"/api/games/{run_id}/build", headers=headers)
    assert r.status_code == 200
    assert auth_store.balance(user.id) == auth_store.INITIAL_CREDITS - 1
    row = db_store.game(run_id)
    assert row["credits_spent"] == 1
    assert row["seconds_granted"] == SECONDS_PER_CREDIT

    # Second enqueue: already charged — no second deduction, no second grant.
    client.post(f"/api/games/{run_id}/build", headers=headers)
    assert auth_store.balance(user.id) == auth_store.INITIAL_CREDITS - 1
    assert db_store.game(run_id)["seconds_granted"] == SECONDS_PER_CREDIT


def test_build_with_no_credits_is_402_and_uncharged(client, monkeypatch):
    user, headers = _user()
    run_id = _make_game(user.id, {"title": "Moon Miner", "mode": "2d", "frozen": True})
    auth_store.deduct(user.id, auth_store.INITIAL_CREDITS, "drain")

    r = client.post(f"/api/games/{run_id}/build", headers=headers)
    assert r.status_code == 402
    assert not db_store.is_charged(run_id)


def test_a_game_out_of_compute_is_402_on_every_gpu_endpoint(client, monkeypatch):
    """Credits buy a grant ONCE; the grant is what each later build/fix/skin spends. A game that
    has burned it must be refused before it takes the GPU slot, not after."""
    user, headers = _user()
    run_id = _make_game(user.id, {"title": "Moon Miner", "mode": "2d", "frozen": True})
    monkeypatch.setattr(bq_mod.build_queue, "enqueue",
                        lambda rid, uid, auto_pause=False, **kw: 0)

    assert client.post(f"/api/games/{run_id}/build", headers=headers).status_code == 200
    db_store.add_seconds_used(run_id, SECONDS_PER_CREDIT)

    for path, body in (("build", None), ("fix", {"note": "a"}), ("assets", None), ("resume", None)):
        r = client.post(f"/api/games/{run_id}/{path}", headers=headers, json=body)
        assert r.status_code == 402, path
        assert r.json()["detail"]["reason"] == "compute_exhausted"

    # Still charged — a refusal is not a refund, and credits are not re-deducted on retry.
    assert auth_store.balance(user.id) == auth_store.INITIAL_CREDITS - 1
    assert db_store.game(run_id)["credits_spent"] == 1


def test_detail_reports_remaining_net_of_queued_work(client):
    """seconds_used lags by the whole depth of the queue, so the bar the client draws from it
    alone reads full while a build's jobs are already spoken for."""
    user, headers = _user()
    run_id = _make_game(user.id, {"title": "Moon Miner", "mode": "2d", "frozen": True})
    db_store.charge_game(run_id, 1, 1000.0)
    db_store.enqueue_job("mesh", {}, game_id=run_id)

    detail = client.get(f"/api/games/{run_id}", headers=headers).json()
    assert detail["seconds_used"] == 0
    assert detail["seconds_remaining"] == 1000.0 - estimate_seconds("mesh")


def test_events_endpoint_replays_the_log(client):
    user, headers = _user()
    run_id = _make_game(user.id, {"title": "Moon Miner", "mode": "2d", "frozen": False})
    db_store.record_event(run_id, "spec_proposed", {"title": "Moon Miner"})
    db_store.record_event(run_id, "build_step", {"step": 1})

    events = client.get(f"/api/games/{run_id}/events", headers=headers).json()
    assert [e["kind"] for e in events] == ["spec_proposed", "build_step"]
    after = events[0]["id"]
    later = client.get(f"/api/games/{run_id}/events?after={after}", headers=headers).json()
    assert [e["kind"] for e in later] == ["build_step"]
