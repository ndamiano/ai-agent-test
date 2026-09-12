"""Pause rides the durable cursor, not process memory, and DEQUEUES the turn it parks.

A build is a chain of jobs: the driver holds nothing between turns, so a control plane that
restarted mid-build must still be able to park the run its workers keep feeding. Leaving the queued
turn in place would pay a GPU for a reply the paused driver then throws away.
"""

import pytest

from maestro.codegen import build_chain, build_state, build_steps
from maestro.codegen.build_state import BuildCursor


@pytest.fixture
def events(monkeypatch):
    seen = []
    monkeypatch.setattr(build_chain, "_emit",
                        lambda kind, run_id, **kw: seen.append((kind, kw)))
    return seen


@pytest.fixture
def dequeued(monkeypatch):
    """How many pending turns pause found. A claimed one is not cancellable, so 0 is the case where
    the worker is already running the turn."""
    count = {"n": 1}
    monkeypatch.setattr(build_chain.jobs, "cancel_pending_build_turn",
                        lambda bid, err: count["n"])
    return count


@pytest.fixture
def run(tmp_path, events, dequeued):
    build_state.save(tmp_path, BuildCursor(build_id="b1", step=3))
    return str(tmp_path)


def test_pause_survives_a_restart(run, tmp_path):
    assert build_chain.pause(run) is True
    assert build_state.load(tmp_path).paused is True


def test_pause_cancels_the_queued_turn_and_refunds_its_step(run, tmp_path):
    """The cancelled turn never ran, so it must not count against the step cap."""
    build_chain.pause(run)
    assert build_state.load(tmp_path).step == 2


def test_a_claimed_turn_keeps_its_step(run, tmp_path, dequeued):
    """Its GPU time is already being paid for — the turn will land and be applied."""
    dequeued["n"] = 0
    build_chain.pause(run)
    assert build_state.load(tmp_path).step == 3


def test_a_paused_build_takes_no_step(run, tmp_path, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("stepped while paused")
    monkeypatch.setattr(build_chain.build_steps, "step", boom)
    build_chain.pause(run)
    build_chain.advance(run)
    assert build_state.load(tmp_path).step == 2


def test_a_re_driven_paused_build_says_nothing(run, tmp_path, events):
    """The regression: the reaper re-drives a build with no turn in flight every few seconds, and a
    park that announced itself each time wrote one feed line per tick, forever."""
    build_chain.pause(run)
    events.clear()
    for _ in range(5):
        build_chain.advance(run)
    assert events == []


def test_a_turn_that_lands_during_a_pause_is_kept(run, tmp_path, events, monkeypatch):
    """A turn claimed before the pause still reports in. Its tool calls are applied and saved — what
    pause withholds is the NEXT turn, not the paid-for one."""
    enqueued = []

    def applies(spec, run_dir, tools, cursor, result, error=None):
        cursor.history.append({"role": "tool", "content": "ok"})
        return build_steps.Infer([], [], 100, report="wrote index.html")

    monkeypatch.setattr(build_chain, "_enqueue_turn", lambda *a, **k: enqueued.append(a))
    monkeypatch.setattr(build_chain.build_steps, "step", applies)
    build_chain.pause(run)
    build_chain.advance(run, {"choices": []})

    assert enqueued == []
    assert build_state.load(tmp_path).history[-1]["content"] == "ok"
    assert [k for k, _ in events][-1] == "build_step"


def test_the_landed_turn_does_not_burn_a_step(run, tmp_path, monkeypatch):
    monkeypatch.setattr(build_chain, "_enqueue_turn", lambda *a, **k: None)
    monkeypatch.setattr(build_chain.build_steps, "step",
                        lambda *a, **k: build_steps.Infer([], [], 100, report="x"))
    build_chain.pause(run)
    build_chain.advance(run, {"choices": []})
    assert build_state.load(tmp_path).step == 2


def test_a_landed_done_ends_the_build_even_paused(run, tmp_path, monkeypatch):
    """Pause withholds the next turn; it does not un-finish a build that just called done."""
    monkeypatch.setattr(build_chain.games, "set_status", lambda *a, **k: None)
    monkeypatch.setattr(build_chain.games, "build_finished", lambda *a, **k: None)
    monkeypatch.setattr(build_chain.build_steps, "step",
                        lambda *a, **k: build_steps.Done("done"))
    build_chain.pause(run)
    build_chain.advance(run, {"choices": []})
    assert build_state.load(tmp_path).phase == "done"


def test_pause_announces_itself_once(run, tmp_path, events):
    build_chain.pause(run)
    assert [k for k, _ in events] == ["build_paused"]


def test_a_build_event_names_its_build(run, tmp_path, events, monkeypatch):
    """A game's log holds its build and every fix, so an event without the id is unattributable."""
    monkeypatch.setattr(build_chain, "_enqueue_turn", lambda *a, **k: None)
    monkeypatch.setattr(build_chain.build_steps, "step",
                        lambda *a, **k: build_steps.Infer([], [], 100, report="x"))
    build_chain.advance(run, {"choices": []})
    build_chain.pause(run)

    assert events and all(kw.get("build_id") == "b1" for _, kw in events)


def test_resume_clears_the_flag_and_announces_it(run, tmp_path, events, monkeypatch):
    monkeypatch.setattr(build_chain, "advance", lambda *a, **k: None)
    build_chain.pause(run)
    events.clear()
    build_chain.resume(run)
    assert build_state.load(tmp_path).paused is False
    assert [k for k, _ in events] == ["build_resumed"]


def test_resume_re_enqueues_the_cancelled_turn(run, tmp_path, monkeypatch):
    """Nothing durable holds the cancelled job's payload — the transcript is the source, so resume
    re-asks from it rather than replaying a stored request."""
    enqueued = []
    monkeypatch.setattr(build_chain, "_enqueue_turn",
                        lambda run_id, cursor, inf: enqueued.append(inf))
    monkeypatch.setattr(build_chain.build_steps, "step",
                        lambda spec, run_dir, tools, cursor, result, error=None: build_steps.Infer(
                            [], [], 100, report="re-sent" if result is None else "applied"))
    build_chain.pause(run)
    build_chain.resume(run)
    assert [i.report for i in enqueued] == ["re-sent"]
    assert build_state.load(tmp_path).step == 3


def test_pause_reports_when_there_is_nothing_in_flight(tmp_path, events):
    """The API answers 409 off this — a finished build has no cursor to park."""
    assert build_chain.pause(str(tmp_path)) is False
    build_state.save(tmp_path, BuildCursor(build_id="b1", phase="done"))
    assert build_chain.pause(str(tmp_path)) is False


def test_status_of_reports_the_pause(run, tmp_path):
    build_chain.pause(run)
    assert build_chain.status_of(run)["paused"] is True
