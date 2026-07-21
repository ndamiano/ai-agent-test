"""SqliteStatsSource over db.store: queue stats, live/stale worker filtering, terminated-clear
on re-register, and the pod_id column shim for a pre-existing db."""

import sqlite3
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from db import store
from scaler.stats import SqliteStatsSource


@pytest.fixture(autouse=True)
def _tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "_db_path", lambda: tmp_path / "platform.db")


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


def test_pod_id_shim_on_a_pre_existing_db(tmp_path, monkeypatch):
    old = tmp_path / "old.db"
    conn = sqlite3.connect(str(old))
    conn.execute(
        "CREATE TABLE workers (id TEXT PRIMARY KEY, queue TEXT, gpu_type TEXT, source TEXT, "
        "busy_seconds REAL NOT NULL DEFAULT 0, started_at REAL NOT NULL, last_seen_at REAL, "
        "terminated_at REAL)")
    conn.execute("INSERT INTO workers (id, queue, started_at, last_seen_at) VALUES (?, ?, ?, ?)",
                 ("veteran", "llm", time.time(), time.time()))
    conn.commit()
    conn.close()

    monkeypatch.setattr(store, "_db_path", lambda: old)
    store.worker_seen("w1", "mesh", pod_id="p1")   # would fail without the ALTER shim
    assert SqliteStatsSource().live_workers("mesh", 60) == [("w1", "p1")]
    assert store.live_workers("llm", 60)[0]["pod_id"] is None
