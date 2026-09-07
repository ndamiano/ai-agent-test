"""Platform db — games ownership/lifecycle, the charge contract, builds, and the event log."""

from pathlib import Path

import pytest
from db import store


def test_create_and_owner():
    store.create_game("g1", "u1")
    assert store.owner_of("g1") == "u1"
    assert store.owner_of("ghost") is None
    row = store.game("g1")
    assert row["status"] == "draft"
    assert row["credits_spent"] == 0


def test_list_is_per_user():
    store.create_game("g1", "u1")
    store.create_game("g2", "u2")
    assert [g["id"] for g in store.list_games("u1")] == ["g1"]


def test_prompt_meta_mirrors_the_title_and_marks_the_run_buildable():
    store.create_game("g1", "u1")
    assert store.game("g1")["status"] == "draft"
    store.update_prompt_meta("g1", "Moon Miner")
    row = store.game("g1")
    assert (row["title"], row["status"]) == ("Moon Miner", "ready")


def test_charge_grants_seconds_and_is_durable():
    store.create_game("g1", "u1")
    assert not store.is_charged("g1")
    store.charge_game("g1", 1, 14_400)
    assert store.is_charged("g1")
    row = store.game("g1")
    assert row["credits_spent"] == 1
    assert row["seconds_granted"] == 14_400
    # An extension stacks; it never resets.
    store.charge_game("g1", 1, 14_400)
    row = store.game("g1")
    assert (row["credits_spent"], row["seconds_granted"]) == (2, 28_800)


def test_build_lifecycle():
    store.create_game("g1", "u1")
    bid = store.create_build("g1", kind="build")
    store.build_started(bid)
    store.build_finished(bid, "succeeded", steps=7)
    (b,) = store.builds_for("g1")
    assert (b["id"], b["kind"], b["status"], b["steps"]) == (bid, "build", "succeeded", 7)
    assert b["queued_at"] <= b["started_at"] <= b["finished_at"]


def test_a_job_on_a_game_must_name_its_build():
    """Cost and history join on the build, so a game's job with no build is a bug at the enqueue,
    not a row to reconcile later. Platform jobs (no game) stay unattributed."""
    store.create_game("g1", "u1")
    store.charge_game("g1", 1, 1000)
    with pytest.raises(ValueError):
        store.enqueue_job("llm", {}, game_id="g1")
    assert store.enqueue_job("llm", {}, game_id="g1", build_id="b1")
    assert store.enqueue_job("llm", {})


def test_a_game_accumulates_builds():
    store.create_game("g1", "u1")
    store.create_build("g1", kind="build")
    store.create_build("g1", kind="fix")
    store.create_build("g1", kind="assets")
    assert [b["kind"] for b in store.builds_for("g1")] == ["build", "fix", "assets"]


def test_events_append_and_replay_after_id():
    store.create_game("g1", "u1")
    store.record_event("g1", "spec_proposed", {"title": "Moon Miner"})
    store.record_event("g1", "build_step", {"step": 1})
    store.record_event("g2", "build_step", {"step": 9})   # another game — never mixed in

    events = store.events_for("g1")
    assert [e["kind"] for e in events] == ["spec_proposed", "build_step"]
    assert events[0]["payload"] == {"title": "Moon Miner"}

    later = store.events_for("g1", after_id=events[0]["id"])
    assert [e["kind"] for e in later] == ["build_step"]


def test_an_event_names_the_build_it_came_from():
    """A game holds its build and every fix after it, so the log is only readable per build."""
    store.create_game("g1", "u1")
    b1 = store.create_build("g1")
    b2 = store.create_build("g1", kind="fix")
    store.record_event("g1", "build_step", {"step": 1}, b1)
    store.record_event("g1", "build_step", {"step": 1}, b2)
    store.record_event("g1", "prompt_proposed", {"title": "Moon Miner"})   # no build yet

    events = store.events_for("g1")
    assert [e["build_id"] for e in events] == [b1, b2, None]


def test_event_payload_survives_non_json_values():
    store.create_game("g1", "u1")
    store.record_event("g1", "weird", {"path": Path("/tmp/x")})   # default=str, never raises
    (e,) = store.events_for("g1")
    assert e["payload"]["path"] == "/tmp/x"


def _build_with_art():
    store.create_game("g1", "u1")
    store.charge_game("g1", 10, 144_000)
    bid = store.create_build("g1", kind="build")
    turn = store.enqueue_job("llm", {}, game_id="g1", build_id=bid,
                             metadata={"stage": "build", "run_id": "g1", "build_id": bid})
    art = store.enqueue_job("image", {}, game_id="g1", build_id=bid, batch_id="b",
                            metadata={"run_id": "g1", "asset_id": "goblin"})
    return bid, turn, art


def test_cancel_pending_build_turn_spares_the_art():
    """Asset renders ride the same build_id as the turn that asked for them, so a pause that failed
    every job under the build would throw away art the model already paid for."""
    bid, turn, art = _build_with_art()
    assert store.cancel_pending_build_turn(bid, "paused by hand") == 1
    assert store.get_job(turn)["status"] == "failed"
    assert store.get_job(art)["status"] == "pending"


def test_a_claimed_turn_is_left_to_its_worker():
    bid, turn, _ = _build_with_art()
    store.claim_job("llm", "w1", 60)
    assert store.cancel_pending_build_turn(bid, "paused by hand") == 0
    assert store.get_job(turn)["status"] == "claimed"


def test_queue_has_work_sees_pending_and_lapsed_leases():
    store.enqueue_job("llm", {"k": 1})
    assert store.queue_has_work("llm") is True
    assert store.queue_has_work("image") is False

    job = store.claim_job("llm", "w1", lease_seconds=60)
    assert store.queue_has_work("llm") is False          # claimed with a live lease

    with store._db() as conn:                            # lapse the lease
        conn.execute("UPDATE jobs SET lease_expires_at = 0 WHERE id = ?", (job["id"],))
    assert store.queue_has_work("llm") is True           # requeue-able counts as work


def test_a_finished_llm_row_keeps_measurements_and_never_the_words():
    store.create_game("g1", "u1")
    store.charge_game("g1", 10, 144_000)
    job_id = store.enqueue_job("llm", {"body": {
        "model": "qwen", "max_tokens": 2048, "reasoning": "medium",
        "messages": [{"role": "system", "content": "You are a game designer."},
                     {"role": "user", "content": "Make a space game"}],
        "tools": [{"name": "draw_sprite"}]}}, game_id="g1", build_id="b1")
    store.worker_seen("w1", "llm")
    store.claim_job("llm", "w1", 60)
    reply = {"choices": [{"message": {"role": "assistant", "content": "I'll create a space game",
                                      "tool_calls": [{"id": "c1", "type": "function", "function": {
                                          "name": "draw_sprite", "arguments": '{"prompt":"ship"}'}}]},
                          "finish_reason": "tool_calls"}],
             "usage": {"prompt_tokens": 50, "completion_tokens": 100, "total_tokens": 150}}
    store.complete_job(job_id, "w1", reply, None, exec_seconds=5.0)

    job = store.get_job(job_id)
    assert job["payload"] == {"model": "qwen", "n_messages": 1, "prompt_chars": 41,
                              "reasoning": "medium", "max_tokens": 2048}
    assert job["result"] == reply    # whole until its consumer has read it

    store.elide_job_result(job_id)
    job = store.get_job(job_id)
    assert job["result"] == {"usage": reply["usage"], "finish_reason": "tool_calls",
                             "tool_names": ["draw_sprite"]}
    assert "ship" not in str(job) and "space game" not in str(job)


def test_a_responses_wire_turn_elides_to_the_same_fields():
    store.create_game("g1", "u1")
    store.charge_game("g1", 10, 144_000)
    job_id = store.enqueue_job("llm", {"body": {
        "model": "m", "max_output_tokens": 900, "reasoning": {"effort": "low"},
        "instructions": "sys", "input": [{"type": "message", "role": "user", "content": "hi"}]}},
        game_id="g1", build_id="b1")
    store.worker_seen("w1", "llm")
    store.claim_job("llm", "w1", 60)
    store.complete_job(job_id, "w1", {"output": [{"type": "function_call", "name": "write",
                                                  "arguments": "{}"}], "status": "completed",
                                      "usage": {"input_tokens": 9}}, None, 1.0)
    store.elide_job_result(job_id)
    job = store.get_job(job_id)
    assert job["payload"] == {"model": "m", "n_messages": 1, "prompt_chars": 5, "reasoning": "low",
                              "max_tokens": 900}
    assert job["result"] == {"usage": {"input_tokens": 9}, "finish_reason": "completed",
                             "tool_names": ["write"]}


def test_an_art_row_keeps_its_prompt_and_render_record_and_drops_the_blobs():
    store.create_game("g1", "u1")
    store.charge_game("g1", 10, 144_000)
    job_id = store.enqueue_job("image", {"kind": "comfy_image",
                                         "workflow": {"p": {"inputs": {"text": "a red dragon"}}},
                                         "uploads": [{"name": "init.png", "b64": "AAAA"}]},
                               game_id="g1", build_id="b1")
    store.worker_seen("w1", "image")
    store.claim_job("image", "w1", 60)
    store.complete_job(job_id, "w1", {"model": "sdxl", "images": [
        {"file": "/blobs/x.png", "safety": {"scores": {"NSFW": 0.1}}, "png_b64": "BBBB"}]},
        None, 5.0)
    store.elide_job_result(job_id)
    job = store.get_job(job_id)
    assert job["payload"] == {"prompt": "a red dragon", "mode": "img2img"}
    assert job["result"] == {"model": "sdxl", "images": [
        {"file": "/blobs/x.png", "safety": {"scores": {"NSFW": 0.1}}}]}
