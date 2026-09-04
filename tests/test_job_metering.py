"""What a completed job records about which model served it. `jobs.model` was written at ENQUEUE
from what the caller asked for and never corrected, so a pod serving one model filed its jobs under
another — 387 of them, on a run that cost real money.
"""

import pytest

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


WK = "NVIDIA RTX PRO 6000 Blackwell Workstation Edition"


def test_a_pricier_card_debits_more_than_one_second_per_second():
    job_id = _job()
    store.complete_job(job_id, "w1", {"out": 1}, None, exec_seconds=10.0, gpu_type=WK)
    game = store.game("g1"); job = store.get_job(job_id)
    assert game["seconds_used"] == pytest.approx(19.1)
    assert (job["exec_seconds"], job["billed_seconds"]) == (10.0, pytest.approx(19.1))


def test_a_5090_debits_one_for_one():
    job_id = _job()
    store.complete_job(job_id, "w1", {"out": 1}, None, exec_seconds=10.0,
                       gpu_type="NVIDIA GeForce RTX 5090")
    assert store.game("g1")["seconds_used"] == 10.0


def test_an_unrated_or_unknown_card_debits_one_for_one():
    """A card with no rate under-bills rather than refusing work; the warning is the signal."""
    for game, gpu in (("g2", "NVIDIA H100 80GB HBM3"), ("g3", None)):
        store.create_game(game, "u1")
        store.charge_game(game, 1, 10_000.0)
        job_id = store.enqueue_job("llm", {}, game_id=game)
        store.worker_seen("w1", "llm")
        store.claim_job("llm", "w1", 60)
        store.complete_job(job_id, "w1", {"out": 1}, None, exec_seconds=4.0, gpu_type=gpu)
        assert store.game(game)["seconds_used"] == 4.0


def test_the_builds_debit_is_weighted_like_the_games():
    build_id = store.create_build("g1")
    job_id = _job(build_id=build_id)
    store.complete_job(job_id, "w1", {"out": 1}, None, exec_seconds=10.0, gpu_type=WK)
    assert store.builds_for("g1")[0]["seconds_used"] == pytest.approx(19.1)


def test_the_job_row_keeps_raw_exec_beside_the_weighted_debit():
    job_id = _job()
    store.complete_job(job_id, "w1", {"out": 1}, None, exec_seconds=10.0, gpu_type=WK)
    job = store.get_job(job_id)
    assert (job["exec_seconds"], job["billed_seconds"]) == (10.0, pytest.approx(19.1))
