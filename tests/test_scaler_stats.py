"""SqliteStatsSource over db.store: queue stats, live/stale worker filtering, and terminated-clear
on re-register."""

from db import store
from scaler.stats import SqliteStatsSource


def _backdate_worker(worker_id, seconds):
    with store._db() as conn:
        conn.execute("UPDATE workers SET last_seen_at = last_seen_at - ? WHERE id = ?",
                     (seconds, worker_id))


def test_queue_stats_counts_and_oldest_age():
    src = SqliteStatsSource()
    assert src.queue_stats("mesh") == (0, 0, None)

    store.enqueue_job("mesh", {"n": 1})
    store.enqueue_job("mesh", {"n": 2})
    st = src.queue_stats("mesh")
    assert (st.pending, st.claimed) == (2, 0)
    assert st.oldest_pending_age_seconds >= 0

    store.claim_job("mesh", "w1", lease_seconds=60)
    st = src.queue_stats("mesh")
    assert (st.pending, st.claimed) == (1, 1)


def test_queue_stats_ignores_other_queues_and_finished_jobs():
    job = store.enqueue_job("mesh", {})
    store.enqueue_job("image", {})
    store.claim_job("mesh", "w1", lease_seconds=60)
    store.complete_job(job, "w1", {"ok": 1}, None, 1.0)
    assert SqliteStatsSource().queue_stats("mesh") == (0, 0, None)


def test_live_and_stale_filtering():
    src = SqliteStatsSource()
    store.worker_seen("w1", "mesh", pod_id="p1")
    assert src.live_workers("mesh", 60) == [("w1", "p1")]
    assert src.stale_workers("mesh", 60) == []

    _backdate_worker("w1", 120)
    assert src.live_workers("mesh", 60) == []
    assert src.stale_workers("mesh", 60) == [("w1", "p1")]


def test_home_box_workers_are_never_stale():
    store.worker_seen("home", "mesh")   # no pod_id
    _backdate_worker("home", 999)
    assert SqliteStatsSource().stale_workers("mesh", 60) == []


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
