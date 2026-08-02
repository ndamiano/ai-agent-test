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


def _billing(rows_hourly, rows_daily, rows_pods):
    return {"hourly_24h": rows_hourly, "daily_30d": rows_daily, "pods_30d": rows_pods}


def test_costs_joins_runpod_billing_against_our_logs(app_client, monkeypatch):
    import time

    from api.routers import admin

    now = time.time()
    iso = admin._iso
    # The wire format: space-separated, NOT the ISO "T" the docs imply.
    wire = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(now - 3600))
    hourly = [{"time": wire, "amount": 0.5, "timeBilledMs": 3_600_000,
               "gpuTypeId": "NVIDIA GeForce RTX 5090"}]
    daily = [{"time": iso(now - 2 * 24 * 3600), "amount": 2.0, "timeBilledMs": 7_200_000,
              "gpuTypeId": "NVIDIA GeForce RTX 5090"},
             {"time": iso(now - 20 * 24 * 3600), "amount": 4.0, "timeBilledMs": 14_400_000,
              "gpuTypeId": "NVIDIA RTX PRO 4500 Blackwell"}]
    pods = [{"podId": "known", "time": iso(now - 3600), "amount": 5.0, "timeBilledMs": 20_000_000},
            {"podId": "boot-looper", "time": iso(now + 60), "amount": 1.5, "timeBilledMs": 1_800_000},
            # Billed before any worker row existed: predates tracking, NOT a ghost.
            {"podId": "prehistoric", "time": iso(now - 29 * 24 * 3600), "amount": 9.0,
             "timeBilledMs": 30_000_000}]
    monkeypatch.setattr(admin, "_billing_rows", lambda _now: _billing(hourly, daily, pods))
    admin._cost_cache.update(at=0.0, data=None)

    db_store.worker_seen("w1", "llm", gpu_type="5090", source="runpod", pod_id="known")
    job_id = db_store.enqueue_job("llm", {"p": 1})
    db_store.claim_job("llm", "w1", lease_seconds=120)
    db_store.complete_job(job_id, "w1", {"ok": True}, None, exec_seconds=600)

    token = _token("root", "admin")
    body = app_client.get("/api/admin/costs",
                          headers={"Authorization": f"Bearer {token}"}).json()

    assert body["runpod_reachable"] is True
    day = next(w for w in body["windows"] if w["label"] == "24h")
    assert day["runpod"]["amount_usd"] == 0.5
    assert day["jobs"]["done"] == 1 and day["jobs"]["exec_seconds"] == 600
    # $0.50 for 1 billed GPU-hour; 600 exec seconds of it used.
    assert day["derived"]["usd_per_gpu_hour"] == 0.5
    assert day["derived"]["utilization"] == round(600 / 3600, 4)
    assert day["derived"]["overhead_seconds"] == 3000

    month = next(w for w in body["windows"] if w["label"] == "30d")
    assert month["runpod"]["amount_usd"] == 6.0
    assert {g["gpu"] for g in month["runpod"]["by_gpu"]} == {
        "NVIDIA GeForce RTX 5090", "NVIDIA RTX PRO 4500 Blackwell"}

    # The boot-looper pod billed money but never registered a worker: ghost spend.
    ghost = body["ghost_30d"]
    assert (ghost["pods"], ghost["amount_usd"], ghost["billed_seconds"]) == (1, 1.5, 1800.0)


def test_costs_without_a_reachable_ledger_still_reports_our_half(app_client, monkeypatch):
    from api.routers import admin

    monkeypatch.setattr(admin, "_billing_rows", lambda _now: None)
    admin._cost_cache.update(at=0.0, data=None)
    token = _token("root2", "admin")
    body = app_client.get("/api/admin/costs",
                          headers={"Authorization": f"Bearer {token}"}).json()
    assert body["runpod_reachable"] is False
    assert body["ghost_30d"] is None
    assert all(w["runpod"] is None and w["derived"] == {} for w in body["windows"])
    assert all("jobs" in w for w in body["windows"])
