"""What a completed job records about which model served it. `jobs.model` was written at ENQUEUE
from what the caller asked for and never corrected, so a pod serving one model filed its jobs under
another — 387 of them, on a run that cost real money.
"""

from db import store


def _job(queue: str = "llm", model=None, build_id=None) -> str:
    store.create_game("g1", "u1")
    store.charge_game("g1", 1, 10_000.0)
    job_id = store.enqueue_job(queue, {}, game_id="g1", build_id=build_id, model=model)
    store.worker_seen("w1", queue)
    store.claim_job(queue, "w1", 60)
    return job_id


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
