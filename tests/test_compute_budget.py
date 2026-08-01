"""The compute budget: enqueue is the one gate every producer of GPU work passes through.

A game gets seconds_granted when it is charged; workers debit seconds_used as jobs complete. The
hole this closes is the window BETWEEN those two: a build enqueues far faster than workers finish,
so measured spend alone reads near-zero right up to the moment a hundred queued jobs land. Enqueue
therefore reserves each job's per-queue estimate and admits against grant − used − reserved.
"""

import threading

import pytest

from db import queue_client, store
from db.estimates import estimate_seconds
from tools.execution_context import run_scope


def _game(seconds: float = 1000.0, game_id: str = "g1") -> str:
    store.create_game(game_id, "u1")
    store.charge_game(game_id, 1, seconds)
    return game_id


def test_remaining_starts_at_the_grant():
    _game(500.0)
    assert store.compute_remaining("g1") == 500.0


def test_pending_jobs_reserve_their_estimate():
    _game(1000.0)
    store.enqueue_job("llm", {}, game_id="g1")
    # Nothing has executed, so seconds_used is still zero — the reservation is the whole point.
    assert store.game("g1")["seconds_used"] == 0
    assert store.compute_remaining("g1") == 1000.0 - estimate_seconds("llm")


def test_completion_replaces_the_reservation_with_measured_seconds():
    _game(1000.0)
    job_id = store.enqueue_job("llm", {}, game_id="g1")
    store.worker_seen("w1", "llm")
    store.claim_job("llm", "w1", 60)
    store.complete_job(job_id, "w1", {"ok": True}, None, exec_seconds=12.0)

    assert store.game("g1")["seconds_used"] == 12.0
    # The estimate is released; only the real 12s is held against the grant.
    assert store.compute_remaining("g1") == 988.0


def test_a_failed_job_does_not_debit_the_game():
    """The user got nothing out of an OOM or a 500. It burned real GPU time, but that is our cost
    to eat, not theirs to pay."""
    _game(1000.0)
    job_id = store.enqueue_job("mesh", {}, game_id="g1")
    store.worker_seen("w1", "mesh")
    store.claim_job("mesh", "w1", 60)
    store.complete_job(job_id, "w1", None, "Status 500: OOM", exec_seconds=180.0)

    assert store.game("g1")["seconds_used"] == 0
    # The reservation is released either way — the job is terminal, nothing more will be spent.
    assert store.compute_remaining("g1") == 1000.0


def test_a_failed_job_still_counts_against_the_worker():
    """busy_seconds measures what WE pay the GPU host for, which is real whether or not the user
    got a result. Never conflate it with what the user is billed."""
    _game(1000.0)
    job_id = store.enqueue_job("llm", {}, game_id="g1")
    store.worker_seen("w1", "llm")
    store.claim_job("llm", "w1", 60)
    store.complete_job(job_id, "w1", None, "boom", exec_seconds=42.0)

    (worker,) = store.live_workers("llm", freshness_seconds=60)
    assert worker["busy_seconds"] == 42.0
    assert store.game("g1")["seconds_used"] == 0


def test_a_partial_failure_is_not_billed_pro_rata():
    """A job that ran a long time and THEN failed is still a non-delivery — the elapsed seconds
    are not a partial entitlement."""
    _game(1000.0)
    job_id = store.enqueue_job("mesh", {}, game_id="g1")
    store.worker_seen("w1", "mesh")
    store.claim_job("mesh", "w1", 600)
    store.complete_job(job_id, "w1", {"partial": True}, "died at 95%", exec_seconds=900.0)

    assert store.game("g1")["seconds_used"] == 0


def test_abandoned_job_releases_its_reservation():
    _game(1000.0)
    job_id = store.enqueue_job("llm", {}, game_id="g1")
    assert store.compute_remaining("g1") < 1000.0
    assert store.abandon_job(job_id, "timed out") is True
    assert store.compute_remaining("g1") == 1000.0
    assert store.abandon_job(job_id, "again") is False


def test_worker_completing_an_abandoned_job_is_dropped():
    """The enqueuer gave up and the reservation was released; a late completion must not then
    debit the game for work nobody is waiting on."""
    _game(1000.0)
    job_id = store.enqueue_job("llm", {}, game_id="g1")
    store.worker_seen("w1", "llm")
    store.claim_job("llm", "w1", 60)
    store.abandon_job(job_id, "timed out")

    assert store.complete_job(job_id, "w1", {"ok": True}, None, exec_seconds=99.0) is None
    assert store.game("g1")["seconds_used"] == 0


def test_enqueue_refuses_when_the_estimate_does_not_fit():
    _game(estimate_seconds("mesh") - 1)
    with pytest.raises(store.InsufficientCompute) as exc:
        store.enqueue_job("mesh", {}, game_id="g1")
    assert exc.value.needed == estimate_seconds("mesh")
    assert exc.value.game_id == "g1"


def test_estimates_are_per_queue_so_a_cheap_job_still_fits():
    """A grant too small for a mesh can still afford an llm call — the gate is per-queue cost,
    not one flat number."""
    _game(estimate_seconds("mesh") - 1)
    assert estimate_seconds("llm") < estimate_seconds("mesh")
    store.enqueue_job("llm", {}, game_id="g1")


def test_reservations_accumulate_until_the_grant_is_gone():
    budget = estimate_seconds("llm") * 3
    _game(budget)
    for _ in range(3):
        store.enqueue_job("llm", {}, game_id="g1")
    assert store.compute_remaining("g1") == 0
    with pytest.raises(store.InsufficientCompute):
        store.enqueue_job("llm", {}, game_id="g1")


def test_measured_overrun_can_push_remaining_negative_but_still_refuses():
    """Estimates are not caps: a job that runs long overdraws. The gate must read the overdraft
    rather than treating a negative balance as headroom."""
    _game(50.0)
    job_id = store.enqueue_job("llm", {}, game_id="g1")
    store.worker_seen("w1", "llm")
    store.claim_job("llm", "w1", 60)
    store.complete_job(job_id, "w1", {}, None, exec_seconds=500.0)

    assert store.compute_remaining("g1") == -450.0
    with pytest.raises(store.InsufficientCompute):
        store.enqueue_job("llm", {}, game_id="g1")


def test_concurrent_enqueues_cannot_all_take_the_same_headroom():
    """Several threads enqueue at once. A check-then-insert that is not one transaction lets every
    thread read the same remaining seconds and each spend it."""
    fits = 4
    _game(estimate_seconds("llm") * fits)
    admitted, refused = [], []
    start = threading.Barrier(12)

    def attempt():
        start.wait()
        try:
            admitted.append(store.enqueue_job("llm", {}, game_id="g1"))
        except store.InsufficientCompute:
            refused.append(1)

    threads = [threading.Thread(target=attempt) for _ in range(12)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(admitted) == fits
    assert len(refused) == 12 - fits
    assert store.compute_remaining("g1") == 0


def test_jobs_outside_a_run_scope_are_ungated():
    """Chat and spec drafting are platform cost, not a game's — no game_id, so no budget to
    check and nothing to refuse."""
    job_id = store.enqueue_job("llm", {}, game_id=None)
    assert store.get_job(job_id)["status"] == "pending"


def test_unknown_game_has_no_budget():
    with pytest.raises(store.InsufficientCompute):
        store.enqueue_job("llm", {}, game_id="ghost")


def test_run_scope_attributes_and_gates_every_producer(monkeypatch):
    """The scope is what ties a job to a game. queue_client reads it for EVERY queue, so image and
    mesh work is metered exactly like llm work."""
    _game(1000.0)
    seen = []
    monkeypatch.setattr(store, "enqueue_job",
                        lambda q, p, **kw: seen.append((q, kw.get("game_id"))) or "j1")
    monkeypatch.setattr(store, "get_job",
                        lambda jid: {"status": "done", "result": {}})

    with run_scope("g1"):
        for queue in ("llm", "image", "mesh"):
            queue_client.run_job(queue, {})

    assert seen == [("llm", "g1"), ("image", "g1"), ("mesh", "g1")]


def test_refused_job_comes_back_as_a_failed_job_not_an_exception():
    """Callers branch on one shape. A budget refusal must degrade like a timeout — the image and
    mesh stages fall back to shapes, and a build fails its gate rather than crashing the loop."""
    _game(1.0)
    with run_scope("g1"):
        job = queue_client.run_job("llm", {})

    assert job["status"] == "failed"
    assert "compute budget exhausted" in job["error"]


def test_timed_out_job_is_abandoned_so_it_stops_holding_seconds(monkeypatch):
    _game(1000.0)
    monkeypatch.setattr(queue_client, "_POLL_INTERVAL", 0.01)
    with run_scope("g1"):
        job = queue_client.run_job("llm", {}, timeout_seconds=0.05)

    assert job["status"] == "failed" and "timed out" in job["error"]
    assert store.compute_remaining("g1") == 1000.0
