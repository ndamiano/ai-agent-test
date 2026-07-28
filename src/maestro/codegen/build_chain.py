"""The build driver — what a finished build LLM turn does next.

A build is a linear chain of `llm`-queue jobs. Each job carries `metadata.stage="build"`; when a
worker completes it, `/worker/complete` routes here. `advance()` applies the completed turn, runs all
local work (tool dispatch, staging) synchronously, and SUSPENDS only at a real inference: it enqueues
one `llm` job and returns; the process is free to die. The next completion reloads build_state.json
and calls `advance` again.

Two phases: `build` (the turn machine writes the game until it calls done or hits the cap) and
`audit` (the frozen brief's claims judged against the source, once, as a REPORT). There is never more
than one llm job in flight per run, so the chain is strictly linear and advance is only ever driven
from the control-plane process — an in-process lock serializes those.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Dict, Optional

from db import store as db_store
from llm_clients.connector import get_connector
from maestro.codegen import audit as audit_mod
from maestro.codegen import build_state, build_steps
from maestro.codegen.build_state import AuditCursor, BuildCursor
from maestro.codegen.staging import entry_path, stage_for_play
from maestro.codegen.tools import build_tools
from maestro.state import RunState
from tools.build_events import _emit

logger = logging.getLogger(__name__)

# One advance at a time per run — the completion handler and the reaper both call advance, and only
# the control-plane process ever does, so an in-process lock is sufficient mutual exclusion.
_locks: Dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def _lock_for(run_id: str) -> threading.Lock:
    with _locks_guard:
        lk = _locks.get(run_id)
        if lk is None:
            lk = _locks[run_id] = threading.Lock()
        return lk


# ── lifecycle ─────────────────────────────────────────────────────────────────
def kickoff(run_id: str, *, kind: str = "build", note: str = "", auto_pause: bool = False,
            max_steps: Optional[int] = None) -> str:
    """Create the build attempt row and start the build. Returns the build_id."""
    if max_steps is None:
        max_steps = 200
    build_id = db_store.create_build(run_id, kind=kind)
    db_store.build_started(build_id)
    start_build(run_id, build_id, kind=kind, note=note, auto_pause=auto_pause, max_steps=max_steps)
    return build_id


def resume(run_id: str) -> None:
    """Re-drive a mid-flight build — a paused one (clear the pause first) or one whose driver process
    died (the cursor is on disk, no llm turn in flight). A no-op once the cursor is done."""
    from maestro.run_control import get as get_control
    ctrl = get_control(run_id)
    if ctrl is not None:
        ctrl.request_resume()
    advance(run_id)


def is_active(run_id: str) -> bool:
    cursor = build_state.load(RunState(run_id).run_dir)
    return cursor is not None and cursor.phase != "done"


def status_of(run_id: str) -> Optional[Dict]:
    cursor = build_state.load(RunState(run_id).run_dir)
    if cursor is None or cursor.phase == "done":
        return None
    return {"kind": cursor.kind}


def start_build(run_id: str, build_id: str, *, kind: str = "build", note: str = "",
                auto_pause: bool = False, max_steps: int = 200) -> None:
    """Kick a build off: write the initial cursor and advance once (which enqueues the first llm
    turn, then returns). A FIX re-enters the same turn machine with the note as its request — the
    model lists and reads the files itself, so there is nothing to hand it up front."""
    rs = RunState(run_id)
    spec = rs.read_spec()
    if spec is None:
        raise ValueError(f"no spec for run {run_id!r}")
    if kind == "build" and not spec.get("frozen"):
        raise RuntimeError("build refuses to run until the spec is frozen")
    _seed(rs)

    prev = build_state.load(rs.run_dir)
    cursor = BuildCursor(build_id=build_id, kind=kind, max_steps=max_steps, t0=time.time())
    if prev is not None:
        # Delivered claims carry across builds, so a later audit reads as a diff rather than a
        # re-litigation of the same code.
        cursor.audit_delivered = list(prev.audit_delivered)
    if kind == "fix":
        cursor.request = ("The game is already written and playable. A person played it and "
                          f"reported this:\n{note}\n\nRead the files and fix exactly that. Call "
                          "done when it is fixed.")
    build_state.save(rs.run_dir, cursor)
    from maestro.run_control import get_or_create
    get_or_create(run_id).set_auto_pause(auto_pause)
    db_store.set_status(run_id, "building")
    _emit("build_started", run_id, n_failing=0, max_steps=max_steps, todo=[], started_at=cursor.t0)
    # wait=True: this first advance must never be dropped by a lock another finalize still holds.
    advance(run_id, wait=True)


def _seed(rs: RunState) -> None:
    """The game folder, holding only the vendored renderer. Everything placed here shows up in the
    model's first `list_files` and steers what it builds, so nothing else is."""
    from maestro.codegen.staging import seed_vendor
    seed_vendor(rs.run_dir)


def on_completion(run_id: str, build_id: str, result: Optional[Dict], error: Optional[str]) -> None:
    """Drive the next step after a build llm turn lands. The result arrives in raw Responses shape off
    the queue — normalize it to chat before applying. A worker-reported error (or a lost result)
    becomes an empty turn the machine handles rather than a stall."""
    raw = result if (error is None and result) else {}
    advance(run_id, get_connector().to_chat(raw))


def advance(run_id: str, result: Optional[Dict] = None, *, wait: bool = False) -> None:
    """Run the state machine forward until it must infer (enqueue + return) or the build finishes.
    Serialized per run; a stale call whose cursor is already done/absent is a no-op. `wait` blocks
    for the lock instead — for a fresh build's FIRST advance, which must never be dropped."""
    lock = _lock_for(run_id)
    if wait:
        lock.acquire()
    elif not lock.acquire(blocking=False):
        return   # another advance (completion or reaper) is already driving this run
    try:
        _advance_locked(run_id, result)
    finally:
        lock.release()


def _advance_locked(run_id: str, result: Optional[Dict]) -> None:
    from maestro.run_control import get as get_control
    rs = RunState(run_id)
    spec = rs.read_spec()
    cursor = build_state.load(rs.run_dir)
    if cursor is None or cursor.phase == "done":
        return
    control = get_control(run_id)
    if control is not None and control.paused:
        control.set_status("paused")
        build_state.save(rs.run_dir, cursor)
        _emit("build_paused", run_id, step=cursor.step)
        return
    tools = build_tools(rs)

    if cursor.phase == "audit":
        outcome = audit_mod.step(spec, rs, cursor, tools, result or {})
    else:
        outcome = build_steps.step(spec, rs.run_dir, tools, cursor, result or {})

    if isinstance(outcome, build_steps.Infer):
        cursor.step += 1
        if cursor.step > cursor.max_steps:
            _finalize(run_id, rs, cursor, ok=_playable(rs.run_dir))
            return
        build_state.save(rs.run_dir, cursor)
        _enqueue_turn(run_id, cursor, outcome)
        if cursor.phase != "done":
            _emit_step(run_id, cursor, outcome.report)
        return

    _emit_step(run_id, cursor, outcome.report)
    if cursor.phase == "build" and _start_audit(rs, cursor):
        build_state.save(rs.run_dir, cursor)
        return _advance_locked(run_id, None)
    _finalize(run_id, rs, cursor, ok=_playable(rs.run_dir) and (cursor.finished or cursor.audit_done))


def _playable(run_dir) -> bool:
    """An index.html is the whole contract: without one there is nothing for a browser to open."""
    return entry_path(run_dir).exists()


def _start_audit(rs: RunState, cursor: BuildCursor) -> bool:
    """The build says it is finished — judge the brief's claims against the source, ONCE, and report.

    Never a fix loop: each fix breaks a claim that already worked, so rounds of judge-then-fix
    converge on sediment rather than a game."""
    if (cursor.kind != "build" or cursor.audit_done or not cursor.finished
            or cursor.step >= cursor.max_steps or not audit_mod.claims_of(rs.read_spec())):
        return False
    cursor.phase = "audit"
    cursor.set_audit(AuditCursor(anchors=list(cursor.audit_delivered)))
    return True


def _finalize(run_id: str, rs: RunState, cursor: BuildCursor, ok: bool) -> None:
    cursor.phase = "done"
    cursor.ok = ok
    build_state.save(rs.run_dir, cursor)
    if ok:
        stage_for_play(rs.run_dir, run_id)
    db_store.set_status(run_id, "built" if ok else "failed")
    if cursor.build_id:
        db_store.build_finished(cursor.build_id, "succeeded" if ok else "failed", steps=cursor.step)
    from maestro.run_control import remove as remove_control
    remove_control(run_id)
    logger.info("build %s finalized: ok=%s steps=%d", run_id, ok, cursor.step)
    _emit("build_done", run_id, ok=ok, steps=cursor.step)


# ── enqueue + events ──────────────────────────────────────────────────────────
def _enqueue_turn(run_id: str, cursor: BuildCursor, inf: "build_steps.Infer") -> None:
    """Land one build llm turn as a pending `llm` job. Its completion re-enters advance. A refused
    compute budget first PREEMPTS the run's queued (unclaimed) asset renders — gameplay beats skin —
    then, still refused, ends the build: a broke run can't spin on refused turns."""
    conn = get_connector()
    payload, model = conn.build_llm_job(inf.messages, inf.schemas, inf.max_tokens, inf.reasoning)

    def _enqueue():
        db_store.enqueue_job("llm", payload, game_id=run_id, build_id=cursor.build_id, model=model,
                             metadata={"stage": "build", "run_id": run_id, "build_id": cursor.build_id})

    try:
        return _enqueue()
    except db_store.InsufficientCompute as e:
        released = db_store.abandon_pending_batch_jobs(
            run_id, "preempted: the build needs the remaining compute")
        if released:
            logger.warning("build %s: preempted %d queued asset job(s) to admit the next turn",
                           run_id, released)
            try:
                return _enqueue()
            except db_store.InsufficientCompute as e2:
                e = e2
        logger.error("build %s turn refused: %s", run_id, e)
        rs = RunState(run_id)
        cursor.phase = "done"
        cursor.ok = False
        build_state.save(rs.run_dir, cursor)
        db_store.set_status(run_id, "failed")
        if cursor.build_id:
            db_store.build_finished(cursor.build_id, "failed", steps=cursor.step)
        from maestro.run_control import remove as remove_control
        remove_control(run_id)
        _emit("build_done", run_id, ok=False, steps=cursor.step, error=f"compute exhausted: {e}")


def _emit_step(run_id: str, cursor: BuildCursor, summary: str) -> None:
    _emit("build_step", run_id, step=cursor.step, max_steps=cursor.max_steps, summary=summary,
          n_failing=0, todo=[], elapsed=time.time() - cursor.t0)
    logger.info("build %s step %d: %s", run_id, cursor.step, summary)
