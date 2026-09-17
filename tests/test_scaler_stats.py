"""SqliteStatsSource over db.jobs + db.workers: queue stats, live/stale worker filtering, and a worker row bound
to its pod."""


import pytest

from db import connection, jobs, workers
from scaler.stats import SqliteStatsSource


def _backdate_worker(worker_id, seconds):
    with connection.platform_db() as conn:
        conn.execute("UPDATE workers SET last_seen_at = last_seen_at - ?1, started_at = started_at - ?1 WHERE id = ?2",
                     (seconds, worker_id))


def test_queue_stats_counts_and_oldest_age():
    src = SqliteStatsSource()
    assert src.queue_stats("mesh") == (0, None)

    jobs.enqueue_job("mesh", {"n": 1})
    jobs.enqueue_job("mesh", {"n": 2})
    assert src.queue_stats("mesh").pending == 2

    jobs.claim_job("mesh", "w1", lease_seconds=60)
    st = src.queue_stats("mesh")
    assert st.pending == 1


def test_queue_stats_ignores_other_queues_and_finished_jobs():
    workers.worker_created("w1", None, "mesh", None, 0.99)
    job = jobs.enqueue_job("mesh", {})
    jobs.enqueue_job("image", {})
    jobs.claim_job("mesh", "w1", lease_seconds=60)
    jobs.complete_job(job, "w1", {"ok": 1}, None, 1.0)
    assert SqliteStatsSource().queue_stats("mesh") == (0, 1.0)


def test_job_seconds_is_the_week_s_mean_with_the_slowest_tenth_left_out():
    workers.worker_created("w1", None, "image", None, 0.99)
    for i, secs in enumerate([2.0] * 9 + [500.0]):
        job = jobs.enqueue_job("image", {"n": i})
        jobs.claim_job("image", "w1", lease_seconds=60)
        jobs.complete_job(job, "w1", {"ok": 1}, None, secs)
    assert SqliteStatsSource().queue_stats("image").job_seconds == 2.0


def test_live_and_stale_filtering():
    src = SqliteStatsSource()
    workers.worker_created("w1", "p1", "mesh", None, 0.99)
    workers.worker_seen("w1", "mesh")
    assert src.live_workers("mesh", 60) == [("w1", "p1")]
    assert src.stale_workers("mesh", 60, 900) == []

    _backdate_worker("w1", 120)
    assert src.live_workers("mesh", 60) == []
    assert src.stale_workers("mesh", 60, 900) == [("w1", "p1")]


def test_home_box_workers_are_never_stale():
    workers.worker_created("home", None, "mesh", None, 0.0)
    workers.worker_seen("home", "mesh")
    _backdate_worker("home", 999)
    assert SqliteStatsSource().stale_workers("mesh", 60, 900) == []


def test_a_deregistered_worker_stays_terminated_when_it_claims_again():
    src = SqliteStatsSource()
    workers.worker_created("w1", "p1", "mesh", None, 0.99)
    workers.worker_seen("w1", "mesh")
    src.mark_worker_terminated("w1")
    workers.worker_seen("w1", "mesh")
    assert src.terminated_workers_with_pods("mesh") == [("w1", "p1")]
    assert src.live_workers("mesh", 60) == []


def test_a_worker_keeps_the_pod_the_scaler_created_it_on():
    workers.worker_created("w1", "p1", "mesh", None, None)
    assert workers.worker_seen("w1", "mesh") is True
    assert workers.live_workers("mesh", 60)[0]["pod_id"] == "p1"
    assert workers.worker_seen("nobody", "mesh") is False


def test_a_created_pod_is_booting_until_its_worker_registers():
    src = SqliteStatsSource()
    workers.worker_created("w1", "p1", "mesh", "NVIDIA GeForce RTX 5090", 0.89)
    assert src.booting_workers("mesh") == [("p1", workers.booting_workers("mesh")[0]["started_at"])]
    assert src.live_workers("mesh", 60) == []
    assert src.stale_workers("mesh", 60, 900) == []
    # The worker registers under the id it was started with: same row, now live, the card its
    # own report.
    workers.worker_seen("w1", "mesh", gpu_type="NVIDIA RTX PRO 4500 Blackwell", source="runpod")
    assert src.booting_workers("mesh") == []
    assert src.live_workers("mesh", 60) == [("w1", "p1")]
    row = workers.live_workers("mesh", 60)[0]
    assert (row["gpu_type"], row["usd_per_hour"], row["source"]) == (
        "NVIDIA RTX PRO 4500 Blackwell", 0.89, "runpod")
    assert row["registered_at"] >= row["started_at"]


def test_a_booting_pod_past_the_deadline_is_stale_and_marking_the_pod_ends_it():
    src = SqliteStatsSource()
    workers.worker_created("w1", "p1", "mesh", None, None)
    _backdate_worker("w1", 1000)
    assert src.stale_workers("mesh", 60, 900) == [("w1", "p1")]
    src.mark_pod_terminated("p1")
    assert src.stale_workers("mesh", 60, 900) == []
    assert src.booting_workers("mesh") == []
