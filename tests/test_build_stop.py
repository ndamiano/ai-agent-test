"""Stopping a build by hand.

A run with no way to end it is a run that stays `building` forever — the frontend shows it as live
and the reaper keeps re-driving it. Stop ends the cursor and keeps whatever the model wrote.
"""
import pytest

from maestro.codegen import build_chain, build_state
from maestro.codegen.build_state import BuildCursor


@pytest.fixture
def calls(monkeypatch):
    seen = {"status": None, "attempt": None, "jobs": [], "staged": False}
    monkeypatch.setattr(build_chain, "_emit", lambda *a, **k: None)
    monkeypatch.setattr(build_chain.db_store, "set_status",
                        lambda run_id, s: seen.__setitem__("status", s))
    monkeypatch.setattr(build_chain.db_store, "build_finished",
                        lambda bid, s, steps=None: seen.__setitem__("attempt", s))
    monkeypatch.setattr(build_chain.db_store, "abandon_build_jobs",
                        lambda bid, err: seen["jobs"].append(bid) or 1)
    monkeypatch.setattr(build_chain, "stage_for_play",
                        lambda *a, **k: seen.__setitem__("staged", True))
    return seen


@pytest.fixture
def run(tmp_path):
    build_state.save(tmp_path, BuildCursor(build_id="b1", step=36))
    return str(tmp_path)


def _playable(tmp_path):
    d = tmp_path / "game"
    d.mkdir(exist_ok=True)
    (d / "index.html").write_text("<html></html>", encoding="utf-8")


def test_stop_ends_the_cursor(run, tmp_path, calls):
    assert build_chain.stop(run) is True
    cursor = build_state.load(tmp_path)
    assert cursor.phase == "done" and cursor.ok is False


def test_a_stopped_build_keeps_a_playable_game(run, tmp_path, calls):
    """Stopping is not discarding — an index.html that exists is still a game."""
    _playable(tmp_path)
    build_chain.stop(run)
    assert build_state.load(tmp_path).ok is True
    assert calls["status"] == "built" and calls["staged"] is True


def test_a_stopped_build_with_nothing_written_failed(run, tmp_path, calls):
    build_chain.stop(run)
    assert calls["status"] == "failed" and calls["staged"] is False


def test_the_attempt_is_recorded_as_stopped_not_succeeded(run, tmp_path, calls):
    """The game is playable; the build still did not finish. The builds row says which."""
    _playable(tmp_path)
    build_chain.stop(run)
    assert calls["attempt"] == "stopped"


def test_stop_fails_the_builds_queued_turns(run, tmp_path, calls):
    build_chain.stop(run)
    assert calls["jobs"] == ["b1"]


def test_a_late_completion_cannot_restart_a_stopped_build(run, tmp_path, calls, monkeypatch):
    """The worker running the last turn still reports in. It must not drive another one."""
    def boom(*a, **k):
        raise AssertionError("stepped after stop")
    build_chain.stop(run)
    monkeypatch.setattr(build_chain.build_steps, "step", boom)
    build_chain.advance(run)
    assert build_state.load(tmp_path).step == 36


def test_stop_reports_when_there_is_nothing_in_flight(tmp_path, calls):
    """The API answers 409 off this."""
    assert build_chain.stop(str(tmp_path)) is False
    build_state.save(tmp_path, BuildCursor(build_id="b1", phase="done"))
    assert build_chain.stop(str(tmp_path)) is False
