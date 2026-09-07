"""The admin queue endpoint: role-gated (a signed-in non-admin gets 403, anonymous 401), and its
snapshot covers every queue — the next jobs in claim order and the fleet with what it holds."""

import time

import pytest
from unittest.mock import MagicMock

from auth import store
from config.settings_manager import settings_manager
from db import store as db_store
from db.estimates import QUEUE_SECONDS


def _token(handle, role):
    store.create_user(handle, "pw-pass1234", role=role, email=f"{handle}@example.com")
    return store.issue_token(store.get_user_by_handle(handle).id)


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
        assert q.keys() >= {"pending", "claimed", "workers_live", "workers_max",
                            "backlog_seconds", "next", "workers"}
    assert body["totals"].keys() == {"pending", "claimed", "workers_live", "backlog_seconds"}


def _queue(app_client, name):
    return _queue_again(app_client, name, _token("root", "admin"))


def _queue_again(app_client, name, token=None):
    token = token or store.issue_token(store.get_user_by_handle("root").id)
    body = app_client.get("/api/admin/queues",
                          headers={"Authorization": f"Bearer {token}"}).json()
    return next(q for q in body["queues"] if q["queue"] == name), body["totals"]


def test_next_lists_pending_jobs_in_claim_order_with_their_wait(app_client):
    db_store.create_game("g1", "u1")
    db_store.charge_game("g1", 1, 10_000)
    build = db_store.create_build("g1")
    first = db_store.enqueue_job("mesh", {}, game_id="g1", build_id=build)
    second = db_store.enqueue_job("mesh", {}, game_id="g1", build_id="b1")

    mesh, totals = _queue(app_client, "mesh")
    assert [j["id"] for j in mesh["next"]] == [first, second]
    assert mesh["next"][0]["game_id"] == "g1"
    assert mesh["next"][0]["build_id"] == build
    assert 0 <= mesh["next"][0]["waiting_seconds"] < 5
    assert mesh["next"][0]["est_seconds"] == QUEUE_SECONDS["mesh"]
    assert (totals["pending"], totals["claimed"]) == (2, 0)


def test_next_is_capped_at_ten(app_client):
    db_store.create_game("g1", "u1")
    db_store.charge_game("g1", 1, 100_000)
    for _ in range(12):
        db_store.enqueue_job("image", {}, game_id="g1", build_id="b1")
    image, _ = _queue(app_client, "image")
    assert (image["pending"], len(image["next"])) == (12, 10)


def test_workers_report_state_card_price_and_the_job_they_hold(app_client, monkeypatch):
    monkeypatch.setattr(
        "config.settings_manager.settings_manager.get_settings",
        lambda s=settings_manager.get_settings(): {
            **s, "billing": {"usd_per_5090_hour": 1.0,
                             "gpu_rates": {"NVIDIA GeForce RTX 5090": 1.0, "BIG": 2.5}}})
    db_store.create_game("g1", "u1")
    db_store.charge_game("g1", 1, 10_000)
    jid = db_store.enqueue_job("mesh", {}, game_id="g1", build_id="b1")
    db_store.worker_seen("busy", "mesh", gpu_type="BIG", source="runpod", pod_id="p1")
    db_store.worker_seen("idle", "mesh", gpu_type="NVIDIA GeForce RTX 5090", source="local")
    db_store.worker_seen("priced", "mesh", gpu_type="BIG", source="runpod", pod_id="p2")
    db_store.set_worker_rate("priced", 2.11)
    db_store.claim_job("mesh", "busy", 60)

    mesh, totals = _queue(app_client, "mesh")
    by_id = {w["id"]: w for w in mesh["workers"]}
    assert by_id["busy"]["state"] == "busy"
    assert by_id["busy"]["usd_per_hour"] == 2.5
    assert by_id["busy"]["pod_id"] == "p1"
    assert by_id["busy"]["job"]["id"] == jid
    assert by_id["busy"]["job"]["game_id"] == "g1"
    assert 0 <= by_id["busy"]["job"]["running_seconds"] < 5
    assert by_id["idle"]["state"] == "idle"
    assert by_id["idle"]["usd_per_hour"] == 1.0
    # The provider's quoted price for the pod, once the scaler has stamped it, beats the table.
    assert by_id["priced"]["usd_per_hour"] == 2.11
    assert by_id["idle"]["job"] is None
    assert (mesh["workers_live"], mesh["claimed"], mesh["pending"]) == (3, 1, 0)
    assert mesh["next"] == []
    assert totals["workers_live"] == 3


def test_a_created_pod_is_booting_until_its_worker_registers(app_client):
    now = time.time()
    db_store.worker_created("p-new", "llm", None, 1.89)
    db_store.worker_created("p-live", "llm", None, 1.89)
    db_store.worker_seen("p-live", "llm", gpu_type="BIG", source="runpod", pod_id="p-live")
    llm, _ = _queue(app_client, "llm")

    states = {w["id"]: w["state"] for w in llm["workers"]}
    assert states == {"p-live": "idle", "p-new": "booting"}
    booting = next(w for w in llm["workers"] if w["state"] == "booting")
    assert booting["pod_id"] == "p-new"
    assert booting["spawned_at"] == pytest.approx(now, abs=5)
    # The price is the pod's from the create, before any worker has said what card it is on.
    assert booting["usd_per_hour"] == 1.89
    assert booting["gpu_type"] is None
    assert llm["workers_live"] == 1


def _billing(rows_hourly, rows_daily, rows_pods):
    return {"hourly_24h": rows_hourly, "daily_30d": rows_daily, "pods_30d": rows_pods}


def _rates(monkeypatch, rates):
    base = settings_manager.get_settings()
    monkeypatch.setattr("config.settings_manager.settings_manager.get_settings",
                        lambda: {**base, "billing": {"usd_per_5090_hour": 1.0, "gpu_rates": rates}})


def test_costs_joins_runpod_billing_against_our_logs_per_card(app_client, monkeypatch):
    from api.routers import admin

    _rates(monkeypatch, {"NVIDIA GeForce RTX 5090": 1.0, "NVIDIA RTX PRO 4500 Blackwell": 0.5})
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

    db_store.worker_seen("w1", "llm", gpu_type="NVIDIA GeForce RTX 5090", source="runpod",
                         pod_id="known")
    db_store.create_game("g1", "u1")
    db_store.charge_game("g1", 1, 100_000)
    build = db_store.create_build("g1")
    job_id = db_store.enqueue_job("llm", {"p": 1}, game_id="g1", build_id=build)
    db_store.claim_job("llm", "w1", lease_seconds=120)
    db_store.complete_job(job_id, "w1", {"ok": True}, None, exec_seconds=600,
                          gpu_type="NVIDIA GeForce RTX 5090")
    db_store.build_finished(build, "built")
    # The game's design ran before the window opened, on another card: it still costs the game.
    design = db_store.enqueue_job("llm", {"p": 0}, game_id="g1", build_id="b1")
    db_store.claim_job("llm", "w1", lease_seconds=120)
    db_store.complete_job(design, "w1", {"ok": True}, None, exec_seconds=1800,
                          gpu_type="NVIDIA RTX PRO 4500 Blackwell")
    with db_store._db() as conn:
        conn.execute("UPDATE jobs SET finished_at = ? WHERE id = ?", (now - 2 * 24 * 3600, design))
    db_store.build_finished(db_store.create_build("g1", kind="change"), "built")
    # A platform job (no game) on a card RunPod has no row for in the 24h bucket.
    pj = db_store.enqueue_job("llm", {"p": 2})
    db_store.claim_job("llm", "w1", lease_seconds=120)
    db_store.complete_job(pj, "w1", {"ok": True}, None, exec_seconds=360,
                          gpu_type="NVIDIA RTX PRO 4500 Blackwell")

    token = _token("root", "admin")
    body = app_client.get("/api/admin/costs",
                          headers={"Authorization": f"Bearer {token}"}).json()

    assert body["runpod_reachable"] is True
    day = next(w for w in body["windows"] if w["label"] == "24h")
    by_gpu = {g["gpu"]: g for g in day["gpus"]}
    # $0.50 for 1 billed GPU-hour; 600 exec seconds of it worked, priced at our $1/h rate.
    assert by_gpu["NVIDIA GeForce RTX 5090"] == {
        "gpu": "NVIDIA GeForce RTX 5090", "alive_seconds": 3600.0, "alive_usd": 0.5,
        "worked_seconds": 600.0, "worked_usd": round(600 / 3600, 4)}
    # Worked with no ledger row: alive unknown, worked still priced at the card's rate.
    assert by_gpu["NVIDIA RTX PRO 4500 Blackwell"] == {
        "gpu": "NVIDIA RTX PRO 4500 Blackwell", "alive_seconds": None, "alive_usd": None,
        "worked_seconds": 360.0, "worked_usd": 0.05}
    # One game built in the window; the average is over everything it ever ran — the 600 s
    # build turn at $1/h plus the 1800 s design from before the window at $0.50/h.
    assert day["games"] == {"n": 1, "avg_gpu_hours": round(2400 / 3600, 4),
                            "avg_usd": round(600 / 3600 + 1800 / 3600 * 0.5, 4),
                            "avg_changes": 1.0}

    month = next(w for w in body["windows"] if w["label"] == "30d")
    by_gpu = {g["gpu"]: g for g in month["gpus"]}
    assert by_gpu["NVIDIA GeForce RTX 5090"]["alive_usd"] == 2.0
    assert by_gpu["NVIDIA RTX PRO 4500 Blackwell"]["alive_usd"] == 4.0

    # The boot-looper pod billed money but never registered a worker: ghost spend.
    ghost = body["ghost_30d"]
    assert (ghost["pods"], ghost["amount_usd"], ghost["billed_seconds"]) == (1, 1.5, 1800.0)


def test_costs_without_a_reachable_ledger_still_reports_our_half(app_client, monkeypatch):
    from api.routers import admin

    monkeypatch.setattr(admin, "_billing_rows", lambda _now: None)
    admin._cost_cache.update(at=0.0, data=None)
    db_store.worker_seen("w1", "llm", gpu_type="NVIDIA GeForce RTX 5090")
    jid = db_store.enqueue_job("llm", {"p": 1})
    db_store.claim_job("llm", "w1", lease_seconds=120)
    db_store.complete_job(jid, "w1", {"ok": True}, None, exec_seconds=60,
                          gpu_type="NVIDIA GeForce RTX 5090")

    token = _token("root2", "admin")
    body = app_client.get("/api/admin/costs",
                          headers={"Authorization": f"Bearer {token}"}).json()
    assert body["runpod_reachable"] is False
    assert body["ghost_30d"] is None
    for w in body["windows"]:
        assert [g["alive_usd"] for g in w["gpus"]] == [None]
        assert w["gpus"][0]["worked_seconds"] == 60.0
        assert w["games"] == {"n": 0, "avg_gpu_hours": None, "avg_usd": None,
                              "avg_changes": None}


def test_a_queue_never_refused_carries_an_empty_stock_out_block(app_client):
    llm, _ = _queue(app_client, "llm")
    assert llm["stockouts"] == {"last_1h": 0, "last_24h": 0, "last_7d": 0, "last_at": None,
                                "last_error": None, "active": False, "active_since": None,
                                "active_count": 0,
                                "totals_60d": {"attempts": 0, "stock_refusals": 0,
                                               "other_refusals": 0, "since": None},
                                "totals_all": {"attempts": 0, "stock_refusals": 0,
                                               "other_refusals": 0, "since": None}}


def test_each_queue_carries_its_stock_outs_and_the_outage_under_way(app_client):
    db_store.record_pod_refusal("llm", "other", [], "401 unauthorized")
    db_store.record_pod_refusal("llm", "stock", [], "no instances currently available")
    db_store.record_pod_refusal("llm", "stock", [], "no instances currently available")
    llm, _ = _queue(app_client, "llm")
    s = llm["stockouts"]
    assert (s["last_1h"], s["last_24h"], s["last_7d"]) == (2, 2, 2)
    assert s["last_error"] == "no instances currently available"
    assert s["active"] and s["active_count"] == 2
    assert s["active_since"] <= s["last_at"]
    db_store.record_pod_created("llm")
    llm, _ = _queue_again(app_client, "llm")
    s = llm["stockouts"]
    assert (s["totals_60d"]["attempts"], s["totals_60d"]["stock_refusals"],
            s["totals_60d"]["other_refusals"]) == (4, 2, 1)
    assert s["totals_all"]["attempts"] == 4 and s["totals_all"]["since"] is not None


def test_a_stock_out_older_than_two_ticks_is_history_not_an_outage(app_client):
    db_store.record_pod_refusal("image", "stock", [], "no instances currently available")
    tick = settings_manager.get_settings()["runpod"]["tick_seconds"]
    with db_store._db() as conn:
        conn.execute("UPDATE pod_refusals SET created_at = created_at - ?", (3 * tick,))
    image, _ = _queue(app_client, "image")
    s = image["stockouts"]
    assert (s["active"], s["active_count"], s["active_since"]) == (False, 0, None)
    assert s["last_7d"] == 1
