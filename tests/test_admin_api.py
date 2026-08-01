"""The admin queue endpoint: role-gated (a signed-in non-admin gets 403, anonymous 401), and its
snapshot shape covers every queue with per-queue + fleet totals."""

from auth import store
from db import store as db_store
from db.estimates import QUEUE_SECONDS


def _token(handle, role):
    store.create_user(handle, "pw", role=role)
    return store.issue_token(store.get_user_by_handle(handle).id)


def test_anonymous_is_rejected(app_client):
    assert app_client.get("/api/admin/queues").status_code == 401


def test_non_admin_is_forbidden(app_client):
    token = _token("alice", "user")
    r = app_client.get("/api/admin/queues", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 403


def test_admin_gets_a_snapshot_of_every_queue(app_client):
    token = _token("root", "admin")
    r = app_client.get("/api/admin/queues", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    body = r.json()
    assert {q["queue"] for q in body["queues"]} == set(QUEUE_SECONDS)
    for q in body["queues"]:
        assert q.keys() >= {"pending", "claimed", "workers_live", "backlog_seconds",
                            "paid_all", "billed_all", "paid_24h", "billed_24h"}
    assert body["totals"].keys() >= {"pending", "workers_live", "paid_all", "billed_all"}


def test_totals_sum_the_queue_rows(app_client):
    db_store.create_game("g1", "u1")
    db_store.charge_game("g1", 1, 10_000)
    jid = db_store.enqueue_job("mesh", {}, game_id="g1")
    db_store.worker_seen("w1", "mesh")
    db_store.claim_job("mesh", "w1", 60)
    db_store.complete_job(jid, "w1", {"ok": True}, None, exec_seconds=100.0)

    token = _token("root", "admin")
    body = app_client.get("/api/admin/queues",
                          headers={"Authorization": f"Bearer {token}"}).json()
    assert body["totals"]["paid_all"] == 100.0
    assert body["totals"]["billed_all"] == 100.0
