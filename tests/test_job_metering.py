"""What a completed job records about itself: which build it cost, and which model served it.

Both were dead columns. `builds.seconds_used` was declared and never written, so per-build cost had
to be reconstructed from `jobs` by hand. `jobs.model` was written at ENQUEUE from what the caller
asked for and never corrected, so a pod serving one model filed its jobs under another — 387 of
them, on a run that cost real money.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from db import store


@pytest.fixture(autouse=True)
def _tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "_db_path", lambda: tmp_path / "platform.db")


def _job(queue: str = "llm", model=None, build_id=None) -> str:
    store.create_game("g1", "u1")
    store.charge_game("g1", 1, 10_000.0)
    job_id = store.enqueue_job(queue, {}, game_id="g1", build_id=build_id, model=model)
    store.worker_seen("w1", queue)
    store.claim_job(queue, "w1", 60)
    return job_id


def _build_row(build_id: str):
    return next(b for b in store.builds_for("g1") if b["id"] == build_id)


def test_a_completed_job_debits_its_build():
    store.create_game("g1", "u1")
    store.charge_game("g1", 1, 10_000.0)
    build_id = store.create_build("g1")
    job_id = store.enqueue_job("llm", {}, game_id="g1", build_id=build_id)
    store.worker_seen("w1", "llm")
    store.claim_job("llm", "w1", 60)

    store.complete_job(job_id, "w1", {"ok": True}, None, exec_seconds=12.0)

    assert _build_row(build_id)["seconds_used"] == 12.0


def test_build_seconds_accumulate_across_turns():
    """A build is a chain of turns; its cost is their sum, not the last one."""
    store.create_game("g1", "u1")
    store.charge_game("g1", 1, 10_000.0)
    build_id = store.create_build("g1")
    store.worker_seen("w1", "llm")
    for seconds in (5.0, 7.0, 3.0):
        job_id = store.enqueue_job("llm", {}, game_id="g1", build_id=build_id)
        store.claim_job("llm", "w1", 60)
        store.complete_job(job_id, "w1", {"ok": True}, None, exec_seconds=seconds)

    assert _build_row(build_id)["seconds_used"] == 15.0


def test_a_failed_job_does_not_debit_its_build():
    """Same rule the game debit follows: only delivered work is billed."""
    store.create_game("g1", "u1")
    store.charge_game("g1", 1, 10_000.0)
    build_id = store.create_build("g1")
    job_id = store.enqueue_job("llm", {}, game_id="g1", build_id=build_id)
    store.worker_seen("w1", "llm")
    store.claim_job("llm", "w1", 60)

    store.complete_job(job_id, "w1", None, "boom", exec_seconds=9.0)

    assert _build_row(build_id)["seconds_used"] == 0


def test_the_served_model_overwrites_what_was_asked_for():
    job_id = _job(model="qwen3.6_27b")

    store.complete_job(job_id, "w1", {"model": "DeepSeek-V4-Flash-0731"}, None, exec_seconds=1.0)

    assert store.get_job(job_id)["model"] == "DeepSeek-V4-Flash-0731"


def test_a_served_model_reported_as_a_path_is_stored_as_its_filename():
    """llama.cpp answers with the gguf path it was launched on; hosted engines answer with a
    name. Storing the basename makes a grouped cost query comparable across both."""
    job_id = _job(model="qwen3.6_27b")

    store.complete_job(job_id, "w1", {"model": "/workspace/models/LLM/MiniMax-M3-UD.gguf"},
                       None, exec_seconds=1.0)

    assert store.get_job(job_id)["model"] == "MiniMax-M3-UD.gguf"


def test_a_result_without_a_model_keeps_the_requested_one():
    """Image and mesh results carry no model field, and must not blank the column."""
    job_id = _job(queue="image", model="sdxl")

    store.complete_job(job_id, "w1", {"images": []}, None, exec_seconds=1.0)

    assert store.get_job(job_id)["model"] == "sdxl"


def test_a_failed_job_keeps_the_requested_model():
    job_id = _job(model="qwen3.6_27b")

    store.complete_job(job_id, "w1", None, "status 500", exec_seconds=1.0)

    assert store.get_job(job_id)["model"] == "qwen3.6_27b"
