"""Build queue behaviour — one build at a time on the single GPU, extras wait with a position."""

import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from api.build_queue import BuildQueue, AlreadyQueued
from maestro import run_control


@pytest.fixture
def q(monkeypatch):
    """A fresh queue whose worker is stopped and whose controls are cleaned up after each test."""
    queue = BuildQueue()
    seen = []
    monkeypatch.setattr("tools.spec_tools._emit",
                        lambda etype, run_id, **f: seen.append({"type": etype, "run_id": run_id, **f}))
    queue.events = seen
    try:
        yield queue
    finally:
        queue.stop()
        for rid in ("a", "b", "c"):
            run_control.remove(rid)


def _gate(run_ids=("a", "b", "c")):
    """A run_build stub that blocks on a per-run Event, so a build can be held 'in flight' while
    others queue behind it. `started[r]` fires when r begins; set `releases[r]` to let r finish."""
    started = {r: threading.Event() for r in run_ids}
    releases = {r: threading.Event() for r in run_ids}
    calls = []

    def fake(run_id, *a, **k):
        calls.append(run_id)
        started[run_id].set()
        releases[run_id].wait(timeout=5)
        run_control.remove(run_id)   # real run_build removes its own control in finally

    return fake, started, releases, calls


def test_cap_one_serializes_and_surfaces_position(q, monkeypatch):
    fake, started, releases, calls = _gate()
    monkeypatch.setattr("maestro.run.run_build", fake)
    q.start()

    assert q.enqueue("a", "u1") == 0            # nothing ahead → builds now
    assert started["a"].wait(timeout=5)
    assert q.enqueue("b", "u1") == 1            # one build in flight → waits behind it

    assert q.state_of("a") == {"status": "building"}
    assert q.state_of("b") == {"status": "queued", "position": 1}
    assert calls == ["a"]                        # b has NOT started while a holds the GPU

    releases["a"].set()
    assert started["b"].wait(timeout=5)          # b runs once a frees the GPU
    assert calls == ["a", "b"]
    releases["b"].set()


def test_double_enqueue_is_rejected(q, monkeypatch):
    fake, started, releases, _ = _gate()
    monkeypatch.setattr("maestro.run.run_build", fake)
    q.start()

    q.enqueue("a", "u1")
    assert started["a"].wait(timeout=5)
    with pytest.raises(AlreadyQueued):
        q.enqueue("a", "u1")                     # already building
    q.enqueue("b", "u1")
    with pytest.raises(AlreadyQueued):
        q.enqueue("b", "u1")                     # already queued
    releases["a"].set()
    releases["b"].set()


def test_position_shifts_as_builds_start(q, monkeypatch):
    fake, started, releases, calls = _gate()
    monkeypatch.setattr("maestro.run.run_build", fake)
    q.start()

    q.enqueue("a", "u1")
    assert started["a"].wait(timeout=5)
    assert q.enqueue("b", "u1") == 1
    assert q.enqueue("c", "u1") == 2

    releases["a"].set()                          # a finishes → b starts, c moves up to position 1
    assert started["b"].wait(timeout=5)
    for _ in range(100):
        if q.state_of("c") == {"status": "queued", "position": 1}:
            break
        time.sleep(0.02)
    assert q.state_of("c") == {"status": "queued", "position": 1}
    releases["b"].set()
    releases["c"].set()


def test_cancel_while_queued_skips_the_build(q, monkeypatch):
    fake, started, releases, calls = _gate()
    monkeypatch.setattr("maestro.run.run_build", fake)
    q.start()

    q.enqueue("a", "u1")
    assert started["a"].wait(timeout=5)
    q.enqueue("b", "u1")
    run_control.get("b").request_cancel()        # cancel b before it leaves the queue

    releases["a"].set()
    for _ in range(100):
        if "a" in calls and q.state_of("b") is None:
            break
        time.sleep(0.02)
    assert calls == ["a"]                         # b's build never ran
    assert not started["b"].is_set()
    assert any(e["type"] == "build_cancelled" and e["run_id"] == "b" for e in q.events)


def test_queued_run_has_a_control_for_pause(q, monkeypatch):
    fake, started, releases, _ = _gate()
    monkeypatch.setattr("maestro.run.run_build", fake)
    q.start()

    q.enqueue("a", "u1")
    assert started["a"].wait(timeout=5)
    q.enqueue("b", "u1")
    # A queued build already has a registered control, so a pause request lands.
    ctrl = run_control.get("b")
    assert ctrl is not None
    ctrl.request_pause()
    assert ctrl.paused is True
    releases["a"].set()
    releases["b"].set()


def test_enqueue_emits_build_queued_with_position(q, monkeypatch):
    monkeypatch.setattr("maestro.run.run_build", lambda *a, **k: None)
    # Worker not started → the run just sits queued so we can read the emitted event deterministically.
    pos = q.enqueue("a", "u1")
    assert pos == 0
    assert q.events[-1] == {"type": "build_queued", "run_id": "a", "position": 0}
