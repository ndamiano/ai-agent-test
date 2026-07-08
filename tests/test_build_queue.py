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
        return _Result(ok=True)      # a completed build

    return fake, started, releases, calls


class _Result:
    """Stand-in for maestro's LoopResult — the queue only reads `.ok`."""
    def __init__(self, ok: bool):
        self.ok = ok


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


# ── charged-once model: the queue NEVER auto-refunds ──────────────────────────
@pytest.fixture
def user(tmp_path, monkeypatch):
    """A ledger user in a tmp auth.db; the queue shares this same store."""
    from auth import store
    monkeypatch.setattr(store, "_db_path", lambda: tmp_path / "auth.db")
    u = store.create_user("alice", "pw")
    return u.id, store


def _wait(pred, timeout=5):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if pred():
            return True
        time.sleep(0.02)
    return False


def test_build_that_raises_keeps_the_charge(q, monkeypatch, user):
    """A run is charged once (durable flag, set at enqueue). A build that dies mid-flight is
    resumable, so the charge stays put — the queue never auto-refunds."""
    uid, store = user

    def boom(run_id, *a, **k):
        run_control.remove(run_id)                   # real run_build clears its control in finally
        raise RuntimeError("build blew up before producing output")
    monkeypatch.setattr("maestro.run.run_build", boom)
    q.start()

    store.deduct(uid, 1, "build", "a")
    after_deduct = store.balance(uid)
    q.enqueue("a", uid)

    assert _wait(lambda: q.state_of("a") is None)   # build drained (raised)
    time.sleep(0.1)                                 # let any (erroneous) refund land
    assert store.balance(uid) == after_deduct       # charge stayed put


def test_build_that_finishes_unmet_keeps_the_charge(q, monkeypatch, user):
    """A build that runs to a non-ok terminal state (stuck-parked / out of steps) is still
    resumable, so the charge stays — no automatic refund."""
    uid, store = user

    def unmet(run_id, *a, **k):
        run_control.remove(run_id)
        return _Result(ok=False)
    monkeypatch.setattr("maestro.run.run_build", unmet)
    q.start()

    store.deduct(uid, 1, "build", "a")
    after_deduct = store.balance(uid)
    q.enqueue("a", uid)

    assert _wait(lambda: q.state_of("a") is None)
    time.sleep(0.1)
    assert store.balance(uid) == after_deduct


def test_successful_build_is_charged_not_refunded(q, monkeypatch, user):
    """A build that completes (result.ok True) keeps the deducted credit."""
    uid, store = user

    def done(run_id, *a, **k):
        run_control.remove(run_id)
        return _Result(ok=True)
    monkeypatch.setattr("maestro.run.run_build", done)
    q.start()

    store.deduct(uid, 1, "build", "a")
    after_deduct = store.balance(uid)
    q.enqueue("a", uid)

    assert _wait(lambda: q.state_of("a") is None)   # build drained
    time.sleep(0.1)                                 # let any (erroneous) refund land
    assert store.balance(uid) == after_deduct       # credit stayed spent
