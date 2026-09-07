"""SqliteStatsSource over db.store: queue stats, live/stale worker filtering, and terminated-clear
on re-register."""

from db import store
from scaler.stats import SqliteStatsSource


def _backdate_worker(worker_id, seconds):
    with store._db() as conn:
        conn.execute("UPDATE workers SET last_seen_at = last_seen_at - ?1, started_at = started_at - ?1 WHERE id = ?2",
                     (seconds, worker_id))


def test_queue_stats_counts_and_oldest_age():
    src = SqliteStatsSource()
    assert src.queue_stats("mesh") == (0, None)

    store.enqueue_job("mesh", {"n": 1})
    store.enqueue_job("mesh", {"n": 2})
    assert src.queue_stats("mesh").pending == 2

    store.claim_job("mesh", "w1", lease_seconds=60)
    st = src.queue_stats("mesh")
    assert st.pending == 1


def test_queue_stats_ignores_other_queues_and_finished_jobs():
    job = store.enqueue_job("mesh", {})
    store.enqueue_job("image", {})
    store.claim_job("mesh", "w1", lease_seconds=60)
    store.complete_job(job, "w1", {"ok": 1}, None, 1.0)
    assert SqliteStatsSource().queue_stats("mesh") == (0, 1.0)


def test_job_seconds_is_the_week_s_mean_with_the_slowest_tenth_left_out():
    for i, secs in enumerate([2.0] * 9 + [500.0]):
        job = store.enqueue_job("image", {"n": i})
        store.claim_job("image", "w1", lease_seconds=60)
        store.complete_job(job, "w1", {"ok": 1}, None, secs)
    assert SqliteStatsSource().queue_stats("image").job_seconds == 2.0


def test_live_and_stale_filtering():
    src = SqliteStatsSource()
    store.worker_seen("w1", "mesh", pod_id="p1")
    assert src.live_workers("mesh", 60) == [("w1", "p1")]
    assert src.stale_workers("mesh", 60, 900) == []

    _backdate_worker("w1", 120)
    assert src.live_workers("mesh", 60) == []
    assert src.stale_workers("mesh", 60, 900) == [("w1", "p1")]


def test_home_box_workers_are_never_stale():
    store.worker_seen("home", "mesh")   # no pod_id
    _backdate_worker("home", 999)
    assert SqliteStatsSource().stale_workers("mesh", 60, 900) == []


def test_terminated_cleared_on_re_register():
    src = SqliteStatsSource()
    store.worker_seen("w1", "mesh", pod_id="p1")
    src.mark_worker_terminated("w1")
    assert src.terminated_workers_with_pods("mesh") == [("w1", "p1")]
    assert src.live_workers("mesh", 60) == []

    # RunPod restarted the container: the same worker re-registers and must not look dead.
    store.worker_seen("w1", "mesh", pod_id="p1")
    assert src.terminated_workers_with_pods("mesh") == []
    assert src.live_workers("mesh", 60) == [("w1", "p1")]


def test_worker_seen_keeps_pod_id_when_not_resent():
    store.worker_seen("w1", "mesh", pod_id="p1")
    store.worker_seen("w1", "mesh")
    assert SqliteStatsSource().live_workers("mesh", 60) == [("w1", "p1")]


def test_a_pod_worker_is_unpriced_until_the_scaler_stamps_its_rate():
    store.worker_seen("w1", "mesh", pod_id="p1")
    store.worker_seen("home", "mesh")
    src = SqliteStatsSource()
    assert src.unpriced_pod_workers() == [("w1", "p1")]
    src.record_worker_rate("w1", 0.89)
    assert src.unpriced_pod_workers() == []
    assert store.live_workers("mesh", 60)[0]["usd_per_hour"] == 0.89


def test_a_created_pod_is_booting_until_its_worker_registers():
    src = SqliteStatsSource()
    store.worker_created("p1", "mesh", "NVIDIA GeForce RTX 5090", 0.89)
    assert src.booting_workers("mesh") == [("p1", store.booting_workers("mesh")[0]["started_at"])]
    assert src.live_workers("mesh", 60) == []
    assert src.stale_workers("mesh", 60, 900) == []
    # The worker registers under the pod id: same row, now live, the card its own report.
    store.worker_seen("p1", "mesh", gpu_type="NVIDIA RTX PRO 4500 Blackwell", source="runpod", pod_id="p1")
    assert src.booting_workers("mesh") == []
    assert src.live_workers("mesh", 60) == [("p1", "p1")]
    row = store.live_workers("mesh", 60)[0]
    assert (row["gpu_type"], row["usd_per_hour"], row["source"]) == (
        "NVIDIA RTX PRO 4500 Blackwell", 0.89, "runpod")
    assert row["registered_at"] >= row["started_at"]


def test_a_booting_pod_past_the_deadline_is_stale_and_marking_the_pod_ends_it():
    src = SqliteStatsSource()
    store.worker_created("p1", "mesh", None, None)
    _backdate_worker("p1", 1000)
    assert src.stale_workers("mesh", 60, 900) == [("p1", "p1")]
    src.mark_pod_terminated("p1")
    assert src.stale_workers("mesh", 60, 900) == []
    assert src.booting_workers("mesh") == []
