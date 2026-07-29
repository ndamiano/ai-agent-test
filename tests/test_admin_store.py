"""The admin-view aggregates over the jobs table: GPU-seconds (paid vs billed) and the projected
backlog. paid counts every finished job we ran; billed counts only delivered, game-attributed work.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from db import store


@pytest.fixture(autouse=True)
def _tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "_db_path", lambda: tmp_path / "platform.db")


def _finish(queue, exec_seconds, *, game_id, error=None):
    """Enqueue → claim → complete one job (the claim is FIFO, so complete whatever it hands back)."""
    store.enqueue_job(queue, {}, game_id=game_id)
    store.worker_seen("w1", queue)
    claimed = store.claim_job(queue, "w1", 60)
    store.complete_job(claimed["id"], "w1", None if error else {"ok": True}, error,
                       exec_seconds=exec_seconds)


def test_gpu_seconds_splits_paid_from_billed():
    store.create_game("g1", "u1")
    store.charge_game("g1", 1, 10_000)

    _finish("llm", 12.0, game_id="g1")               # delivered → paid + billed
    _finish("llm", 5.0, game_id="g1", error="boom")  # failed    → paid only
    _finish("llm", 7.0, game_id=None)                # platform job (chat) → paid only

    agg = store.gpu_seconds("llm")
    assert agg["paid"] == 24.0
    assert agg["billed"] == 12.0


def test_gpu_seconds_is_per_queue():
    store.create_game("g1", "u1")
    store.charge_game("g1", 1, 10_000)
    _finish("llm", 12.0, game_id="g1")
    _finish("mesh", 100.0, game_id="g1")

    assert store.gpu_seconds("llm")["paid"] == 12.0
    assert store.gpu_seconds("mesh")["paid"] == 100.0
    assert store.gpu_seconds("image")["paid"] == 0.0


def test_gpu_seconds_window_bounds_on_finished_at():
    import time
    store.create_game("g1", "u1")
    store.charge_game("g1", 1, 10_000)
    _finish("llm", 12.0, game_id="g1")

    assert store.gpu_seconds("llm", since=time.time() + 100)["paid"] == 0.0
    assert store.gpu_seconds("llm", since=0)["paid"] == 12.0


def test_backlog_seconds_sums_unfinished_estimates():
    store.create_game("g1", "u1")
    store.charge_game("g1", 1, 10_000)
    # llm est is 30s/job; two pending → 60.
    store.enqueue_job("llm", {}, game_id="g1")
    store.enqueue_job("llm", {}, game_id="g1")
    assert store.backlog_seconds("llm") == 60.0

    store.worker_seen("w1", "llm")
    claimed = store.claim_job("llm", "w1", 60)
    store.complete_job(claimed["id"], "w1", {"ok": True}, None, exec_seconds=12.0)
    assert store.backlog_seconds("llm") == 30.0
