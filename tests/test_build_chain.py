"""build_chain — the completion-driven build driver.

The end-to-end drive (plan → data → author → gates → fix, to green) is exercised in test_codegen via
the harness. These tests pin the driver MECHANICS that replace the old in-memory build queue: a turn
is a stage-tagged `llm` job, pause halts the chain, a refused budget ends the build, and the reaper's
stuck-build query finds a run whose driver died mid-turn.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from db import store as db_store
from maestro.codegen import build_chain, build_state
from maestro.run_control import get_or_create, remove
from maestro.state import RunState

_SPEC = {"frozen": True, "mode": "2d", "design": {"control": {"scheme": "top-down"}}}


class FakeConn:
    """Builds a job payload carrying the raw messages; every turn's reply is unparseable, which is
    enough to exercise the driver's plumbing (plan falls back, etc.)."""

    def build_llm_job(self, messages, schemas=None, max_tokens=None, reasoning=None):
        return {"messages": messages, "tools": schemas, "reasoning": reasoning}, "model"

    def to_chat(self, raw):
        return raw

    def generate_with_tools(self, messages, tools=None, **kw):
        return {"choices": [{"message": {"content": "garbage"}}]}


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(db_store, "_db_path", lambda: tmp_path / "platform.db")
    monkeypatch.setattr(build_chain, "stage_for_play", lambda *a, **k: None)
    monkeypatch.setattr(build_chain, "get_connector", lambda: FakeConn())
    run_id = str(tmp_path)
    db_store.create_game(run_id, "u1")
    RunState(run_id).write_spec(_SPEC)
    yield run_id, tmp_path
    remove(run_id)


def test_kickoff_enqueues_a_stage_tagged_build_turn(env, monkeypatch):
    run_id, _ = env
    captured = []
    monkeypatch.setattr(db_store, "enqueue_job",
                        lambda queue, payload, **kw: captured.append((queue, kw.get("metadata"))) or "j")

    build_chain.kickoff(run_id, kind="build")

    assert len(captured) == 1                          # exactly one turn in flight
    queue, meta = captured[0]
    assert queue == "llm"
    assert meta["stage"] == "build" and meta["run_id"] == run_id and meta["build_id"]
    assert db_store.game(run_id)["status"] == "building"
    assert build_chain.is_active(run_id) is True


def test_pause_halts_the_chain_without_enqueuing(env, monkeypatch):
    run_id, run_dir = env
    captured = []
    monkeypatch.setattr(db_store, "enqueue_job",
                        lambda queue, payload, **kw: captured.append(1) or "j")
    build_chain.kickoff(run_id, kind="build")          # turn 1 enqueued
    assert len(captured) == 1

    get_or_create(run_id).request_pause()
    events = []
    monkeypatch.setattr(build_chain, "_emit", lambda et, rid, **f: events.append(et))
    build_chain.advance(run_id, {"choices": [{"message": {"content": "garbage"}}]})

    assert "build_paused" in events
    assert len(captured) == 1                           # no new turn while paused
    assert build_state.load(run_dir).phase != "done"   # the build is suspended, not finished


def test_a_refused_budget_ends_the_build(env, monkeypatch):
    run_id, run_dir = env

    def refuse(queue, payload, **kw):
        raise db_store.InsufficientCompute(run_id, 0.0, 10.0)

    monkeypatch.setattr(db_store, "enqueue_job", refuse)
    build_chain.kickoff(run_id, kind="build")

    assert db_store.game(run_id)["status"] == "failed"
    cursor = build_state.load(run_dir)
    assert cursor.phase == "done" and cursor.ok is False


def test_stuck_builds_flags_a_run_with_no_inflight_turn(env):
    run_id, _ = env
    db_store.charge_game(run_id, 1, 10_000)
    jid = db_store.enqueue_job("llm", {"path": "x"}, game_id=run_id, build_id="b",
                               metadata={"stage": "build", "run_id": run_id, "build_id": "b"})
    db_store.claim_job("llm", "w", 60)
    db_store.complete_job(jid, "w", {"ok": True}, None, 1.0)   # the turn is terminal
    db_store.set_status(run_id, "building")

    # No pending/claimed build turn + a building status => stuck (grace<0 so the just-finished job
    # counts as old).
    assert run_id in db_store.stuck_builds(-1)

    # A pending build turn means it is NOT stuck — the chain is alive.
    db_store.enqueue_job("llm", {"path": "y"}, game_id=run_id, build_id="b",
                         metadata={"stage": "build", "run_id": run_id, "build_id": "b"})
    assert run_id not in db_store.stuck_builds(-1)


def test_reaper_readvance_of_a_mid_fix_build_does_not_crash(env, monkeypatch):
    """The reaper re-drives a stuck build via `advance(run_id)` with NO result — the completion that
    would have carried one was lost. A build suspended mid-fix must survive that: the lost turn
    becomes an empty turn the fix shape retries, not a NoneType crash that aborts the reaper tick."""
    run_id, run_dir = env
    db_store.charge_game(run_id, 1, 10_000)
    captured = []
    monkeypatch.setattr(db_store, "enqueue_job",
                        lambda queue, payload, **kw: captured.append(1) or "j")

    build_chain.kickoff(run_id, kind="build")            # turn 1 enqueued; cursor now mid-fix
    assert build_state.load(run_dir).phase == "fix"
    assert len(captured) == 1

    # Reaper path: no result to apply. Must not raise.
    build_chain.advance(run_id)

    cursor = build_state.load(run_dir)
    assert cursor.phase != "done"                        # re-driven, not wedged
