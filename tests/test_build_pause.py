"""Pause rides the durable cursor, not process memory.

A build is a chain of jobs: the driver holds nothing between turns, so a control plane that
restarted mid-build must still be able to park the run its workers keep feeding.
"""
import pytest

from maestro.codegen import build_chain, build_state
from maestro.codegen.build_state import BuildCursor


@pytest.fixture
def run(tmp_path, monkeypatch):
    monkeypatch.setattr(build_chain, "_emit", lambda *a, **k: None)
    build_state.save(tmp_path, BuildCursor(build_id="b1", step=3))
    return str(tmp_path)


def test_pause_survives_a_restart(run, tmp_path):
    assert build_chain.pause(run) is True
    assert build_state.load(tmp_path).paused is True


def test_a_paused_build_takes_no_step(run, tmp_path, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("stepped while paused")
    monkeypatch.setattr(build_chain.build_steps, "step", boom)
    build_chain.pause(run)
    build_chain.advance(run)
    assert build_state.load(tmp_path).step == 3


def test_resume_clears_the_flag(run, tmp_path, monkeypatch):
    monkeypatch.setattr(build_chain, "advance", lambda *a, **k: None)
    build_chain.pause(run)
    build_chain.resume(run)
    assert build_state.load(tmp_path).paused is False


def test_pause_reports_when_there_is_nothing_in_flight(tmp_path):
    """The API answers 409 off this — a finished build has no cursor to park."""
    assert build_chain.pause(str(tmp_path)) is False
    build_state.save(tmp_path, BuildCursor(build_id="b1", phase="done"))
    assert build_chain.pause(str(tmp_path)) is False


def test_status_of_reports_the_pause(run, tmp_path):
    build_chain.pause(run)
    assert build_chain.status_of(run)["paused"] is True
