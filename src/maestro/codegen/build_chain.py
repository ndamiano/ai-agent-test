"""The build driver — what a finished build LLM turn does next.

A build is a linear chain of `llm`-queue jobs. Each job carries `metadata.stage="build"`; when a
worker completes it, `/worker/complete` routes here. `advance()` applies the completed turn, runs all
local work (tool dispatch, staging) synchronously, and SUSPENDS only at a real inference: it enqueues
one `llm` job and returns; the process is free to die. The next completion reloads build_state.json
and calls `advance` again.

The turn machine writes the game until it calls done or hits the step cap. There is never more than
one llm job in flight per run, so the chain is strictly linear and advance is only ever driven from
the control-plane process — an in-process lock serializes those.
"""

from __future__ import annotations

import logging
import shutil
import threading
import time
from typing import Dict, Optional

from db import store as db_store
from llm_clients.connector import get_connector
from maestro.codegen import (artifact_screen, asset_use, build_state, build_steps, error_gate,
                             play_gate, snapshots, turn_log)
from maestro.codegen.build_state import DEFAULT_MAX_STEPS, BuildCursor
from maestro.codegen.staging import entry_path, game_dir, stage_for_play
from maestro.codegen.tools import build_tools
from maestro.state import RunState
from tools.build_events import _emit
from tools.safety import log_violation

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


def kickoff(run_id: str, *, kind: str = "build", note: str = "", fresh: bool = False,
            max_steps: int = DEFAULT_MAX_STEPS) -> str:
    """Create the build attempt row and start the build. Returns the build_id.

    `fresh` starts over an EMPTY game folder — the from-scratch button, and only that. A plain
    re-trigger and a resume carry the folder forward, and a fix edits it on purpose."""
    build_id = db_store.create_build(run_id, kind=kind)
    db_store.build_started(build_id)
    start_build(run_id, build_id, kind=kind, note=note, fresh=fresh, max_steps=max_steps)
    return build_id


def pause(run_id: str) -> bool:
    """Park a build and DEQUEUE its turn. The flag rides the durable cursor, not process memory: the
    driver holds no state between turns, so a control plane that restarted mid-build still owns the
    run. False when there is nothing in flight to pause.

    A queued turn is cancelled and its step refunded, since it never ran. A CLAIMED one is left to
    its worker — that GPU time is already being paid for, so the result is applied when it lands and
    the build parks after it."""
    lock = _lock_for(run_id)
    lock.acquire()   # an advance mid-flight must finish enqueueing before its job can be cancelled
    try:
        rs = RunState(run_id)
        cursor = build_state.load(rs.run_dir)
        if cursor is None or cursor.phase == "done":
            return False
        cursor.paused = True
        if cursor.build_id and db_store.cancel_pending_build_turn(cursor.build_id, "paused by hand"):
            cursor.step = max(0, cursor.step - 1)
        build_state.save(rs.run_dir, cursor)
        _emit("build_paused", run_id, build_id=cursor.build_id, step=cursor.step)
        return True
    finally:
        lock.release()


def stop(run_id: str) -> bool:
    """End everything the run has in flight, at whatever stage it is in, and keep what it wrote.
    Every job the run owns on every queue is failed — the design's llm turn, the build's, the art
    behind it, a world's legs — claimed ones included, since the worker holding one may be the
    thing that hung; every build row still open records `stopped`, which is what refuses a job a
    lingering thread enqueues afterwards. False when there was nothing in flight at all.

    A build with a cursor is finalized where it stands: playability is judged the same way a build
    that hits its step cap is judged — an index.html is still the whole contract, and a run
    stopped by hand is not a run that failed. A run still being DESIGNED has nothing to judge: its
    ask lands as its prompt, the fallback every design that never lands takes, and the run ends
    `failed` so the page has a prompt to rebuild from rather than a design that never comes."""
    lock = _lock_for(run_id)
    lock.acquire()
    try:
        rs = RunState(run_id)
        cursor = build_state.load(rs.run_dir)
        cancelled = db_store.abandon_game_jobs(run_id, "the run was stopped by hand")
        live = cursor is not None and cursor.phase != "done"
        if live:
            _finalize(run_id, rs, cursor, ok=_playable(rs.run_dir), attempt="stopped")
        ended = db_store.finish_open_builds(run_id, "stopped")
        if any(b["kind"] == "design" for b in ended):
            _end_design(run_id, rs, next(b["id"] for b in ended if b["kind"] == "design"))
        return live or bool(cancelled) or bool(ended)
    finally:
        lock.release()


def _end_design(run_id: str, rs: RunState, build_id: str) -> None:
    from maestro.codegen.run import set_prompt
    spec = rs.read_spec() or {}
    if not spec.get("request") and spec.get("ask"):
        set_prompt(run_id, spec["ask"], event="prompt_proposed")
    db_store.set_status(run_id, "failed")
    _emit("build_done", run_id, build_id=build_id, ok=False, steps=0)


def resume(run_id: str) -> None:
    """Re-drive a mid-flight build — a paused one (clear the pause first) or one whose driver process
    died (the cursor is on disk, no llm turn in flight). A no-op once the cursor is done."""
    rs = RunState(run_id)
    cursor = build_state.load(rs.run_dir)
    if cursor is not None and cursor.paused:
        cursor.paused = False
        build_state.save(rs.run_dir, cursor)
        _emit("build_resumed", run_id, build_id=cursor.build_id, step=cursor.step)
    advance(run_id)


def is_active(run_id: str) -> bool:
    cursor = build_state.load(RunState(run_id).run_dir)
    return cursor is not None and cursor.phase != "done"


def status_of(run_id: str) -> Optional[Dict]:
    cursor = build_state.load(RunState(run_id).run_dir)
    if cursor is None or cursor.phase == "done":
        return None
    return {"kind": cursor.kind, "paused": cursor.paused}


def start_build(run_id: str, build_id: str, *, kind: str = "build", note: str = "",
                fresh: bool = False, max_steps: int = DEFAULT_MAX_STEPS) -> None:
    """Kick a build off: write the initial cursor and advance once (which enqueues the first llm
    turn, then returns). A FIX (the error gate's note) and a CHANGE (a person's note after playing)
    both re-enter the same turn machine with the note as its request — the model lists and reads
    the files itself, so there is nothing to hand it up front."""
    rs = RunState(run_id)
    if rs.read_spec() is None:
        raise ValueError(f"no prompt for run {run_id!r}")
    if fresh:
        _clear_game(rs)
    _seed(rs)

    cursor = BuildCursor(build_id=build_id, kind=kind, max_steps=max_steps, t0=time.time())
    if kind == "fix":
        snapshots.take(rs.run_dir, "before-fix")
        cursor.request = ("The game is already written and playable. A person played it and "
                          f"reported this:\n{note}\n\nRead the files and fix exactly that. Call "
                          "done when it is fixed.")
    elif kind == "change":
        snapshots.take(rs.run_dir, "before-change")
        cursor.request = ("The game is already written and playable. A person played it and asked "
                          f"for this change:\n{note}\n\nRead the files and make exactly that "
                          "change, keeping everything else playing as it does. Call done when it "
                          "is made.")
    build_state.save(rs.run_dir, cursor)
    db_store.set_status(run_id, "building")
    _emit("build_started", run_id, build_id=build_id, max_steps=max_steps, started_at=cursor.t0)
    # wait=True: this first advance must never be dropped by a lock another finalize still holds.
    advance(run_id, wait=True)


def _clear_game(rs: RunState) -> None:
    """Empty the game folder before a fresh build, and stop the art the last one is still waiting on.

    A second attempt at the same prompt otherwise opens on the dead build's half-written files: the
    model reads them, believes them, and re-asks for art it already has under new ids (measured
    2026-08-01: three naming schemes for one cast, 40 renders, no finished game). Snapshotted
    first — the history is in git, so `--restore` still reaches what this deletes."""
    d = game_dir(rs.run_dir)
    if not d.exists():
        return
    snapshots.take(rs.run_dir, "before-rebuild")
    # A render still in flight would land in the new build's folder and write itself into a manifest
    # that no longer asked for it.
    db_store.abandon_pending_batch_jobs(rs.run_id, "superseded: the game was rebuilt")
    shutil.rmtree(d)


def _seed(rs: RunState) -> None:
    """The game folder, holding only the vendored renderer. Everything placed here shows up in the
    model's first `list_files` and steers what it builds, so nothing else is."""
    from maestro.codegen.staging import seed_vendor
    seed_vendor(rs.run_dir)


def on_completion(run_id: str, build_id: str, result: Optional[Dict], error: Optional[str],
                  job_id: str, exec_seconds: float) -> None:
    """Drive the next step after a build llm turn lands, and ARCHIVE the turn that just landed. A
    worker-reported error is carried THROUGH: a turn the server refused is not a turn that answered
    with nothing, and the model can only act on the difference if it is told which one happened."""
    advance(run_id, result if (error is None and result) else {},
            landed={"job_id": job_id, "exec_seconds": exec_seconds, "error": error})


def advance(run_id: str, result: Optional[Dict] = None, *, wait: bool = False,
            landed: Optional[Dict] = None) -> None:
    """Run the state machine forward until it must infer (enqueue + return) or the build finishes.
    Serialized per run; a stale call whose cursor is already done/absent is a no-op. `wait` blocks
    for the lock instead — for a fresh build's FIRST advance, which must never be dropped.

    `landed` is the completed turn's {job_id, exec_seconds, error} — the job whose body this
    advance archives. A re-drive (reaper, resume) has no turn to archive and passes none."""
    lock = _lock_for(run_id)
    if wait:
        lock.acquire()
    elif not lock.acquire(blocking=False):
        return   # another advance (completion or reaper) is already driving this run
    try:
        _advance_locked(run_id, result, landed)
    finally:
        lock.release()


def _advance_locked(run_id: str, result: Optional[Dict], landed: Optional[Dict] = None) -> None:
    rs = RunState(run_id)
    spec = rs.read_spec()
    cursor = build_state.load(rs.run_dir)
    if cursor is None or cursor.phase == "done":
        return
    if landed:
        _archive_turn(rs, cursor, landed, result)
    if cursor.paused and result is None:
        return   # nothing to apply, and a re-drive (reaper, resume race) must not restart a parked build
    tools = build_tools(rs, cursor.build_id)
    outcome = build_steps.step(spec, rs.run_dir, tools, cursor, result,
                               error=(landed or {}).get("error"))

    if isinstance(outcome, build_steps.Infer):
        if cursor.paused:
            # A turn claimed before the pause. Its work is kept; what pause withholds is the NEXT one.
            build_state.save(rs.run_dir, cursor)
            _emit_step(run_id, cursor, outcome.report)
            return
        cursor.step += 1
        if cursor.step > cursor.max_steps:
            _finalize(run_id, rs, cursor, ok=_playable(rs.run_dir))
            return
        if not cursor.meta_logged:
            turn_log.append_meta(
                rs.run_dir, run_id=run_id, build_id=cursor.build_id, system=cursor.system,
                tools=outcome.schemas, model=get_connector().model_name,
                max_tokens=outcome.max_tokens, reasoning=get_connector().reasoning,
                # The control plane enqueues the canonical chat shape and nothing else
                # (llm_clients/wire.py); the record says so rather than a reader inferring it from
                # whatever `llm.api` happens to be the day the log is read.
                wire="chat")
            cursor.meta_logged = True
        build_state.save(rs.run_dir, cursor)
        _enqueue_turn(run_id, cursor, outcome)
        # A refused compute budget ends the run inside _enqueue_turn; a step line after that
        # `build_done` would read as progress the build never made.
        if cursor.phase != "done":
            _emit_step(run_id, cursor, outcome.report)
        return

    _emit_step(run_id, cursor, outcome.report)
    _finalize(run_id, rs, cursor, ok=_playable(rs.run_dir) and cursor.finished)


def _archive_turn(rs: RunState, cursor: BuildCursor, landed: Dict, result: Optional[Dict]) -> None:
    """Write the landed turn to the run dir's log. A build turn's request is the whole transcript,
    and the jobs row keeps only its measurements (`db_store.elide_payload`); the log stores each
    message once — everything appended since the last archive is exactly what this turn's request
    added to the one before it, and the cursor holds the live copy until it is appended."""
    message = ((result or {}).get("choices") or [{}])[0].get("message")
    turn_log.append_turn(rs.run_dir, turn=cursor.turn, job_id=landed["job_id"],
                         added=cursor.history[cursor.logged:], response=message,
                         usage=(result or {}).get("usage") or {},
                         exec_seconds=landed["exec_seconds"], error=landed["error"])
    cursor.logged = len(cursor.history)


def _playable(run_dir) -> bool:
    """An index.html is the whole contract: without one there is nothing for a browser to open."""
    return entry_path(run_dir).exists()


def _finalize(run_id: str, rs: RunState, cursor: BuildCursor, ok: bool,
              attempt: Optional[str] = None) -> None:
    """`ok` is whether the GAME is playable; `attempt` is how the build ended, and they differ for
    one stopped by hand over a game that already ran."""
    held = artifact_screen.screen_artifact(rs.run_dir) if ok else None
    cursor.phase = "done"
    cursor.ok = ok and held is None
    build_state.save(rs.run_dir, cursor)
    if held is not None:
        # A held game exists only in its run dir: not staged, not snapshotted, not archived. The
        # owner sees a neutral status; the violation row is what the admin panel reads.
        path, violation = held
        log_violation(violation, run_id=run_id, source=f"artifact:{path}")
        db_store.set_status(run_id, "held")
        if cursor.build_id:
            db_store.build_finished(cursor.build_id, "held", steps=cursor.step)
        logger.warning("build %s held: artifact screen hit in %s", run_id, path)
        _emit("build_done", run_id, build_id=cursor.build_id, ok=False, steps=cursor.step,
              held=True)
        return
    if ok:
        stage_for_play(rs.run_dir, run_id)
        snapshots.take(rs.run_dir, "built")
    db_store.set_status(run_id, "built" if ok else "failed")
    if cursor.build_id:
        db_store.build_finished(cursor.build_id, attempt or ("succeeded" if ok else "failed"),
                                steps=cursor.step)
    art = asset_use.audit(rs.run_dir)
    if art["unreferenced"] or art["missing"]:
        logger.warning("build %s art: %d asked for and never loaded, %d loaded and never asked for",
                       run_id, len(art["unreferenced"]), len(art["missing"]))
    logger.info("build %s finalized: ok=%s steps=%d", run_id, ok, cursor.step)
    _emit("build_done", run_id, build_id=cursor.build_id, ok=ok, steps=cursor.step,
          art_unreferenced=art["unreferenced"], art_missing=art["missing"])
    if ok and attempt is None:
        # Post-finalize runs AFTER the lock this finalize holds is released — a kickoff blocks on
        # the same lock, so running it inline here would deadlock. A build stopped by hand gets
        # no gate: the human ended it, and an auto-build would restart what they stopped.
        threading.Thread(target=_post_finalize, args=(run_id, cursor.build_id), daemon=True).start()


def _post_finalize(run_id: str, build_id: str) -> None:
    """The error gate, then the play gate, then the archive. A gate fix's own finalize re-enters
    here, so only a SETTLED chain (no fix kicked by either gate) is archived — the bucket holds
    finished games rather than one snapshot per intermediate."""
    from maestro.codegen import archive
    if not error_gate.after_build(run_id, build_id) and not play_gate.after_build(run_id, build_id):
        archive.archive(run_id)


def _enqueue_turn(run_id: str, cursor: BuildCursor, inf: "build_steps.Infer") -> None:
    """Land one build llm turn as a pending `llm` job. Its completion re-enters advance. A refused
    compute budget first PREEMPTS the run's queued (unclaimed) asset renders — gameplay beats skin —
    then, still refused, ends the build: a broke run can't spin on refused turns."""
    conn = get_connector()
    payload, model = conn.build_llm_job(inf.messages, inf.schemas, inf.max_tokens)

    def _enqueue():
        db_store.enqueue_job("llm", payload, game_id=run_id, build_id=cursor.build_id, model=model,
                             metadata={"stage": "build", "run_id": run_id, "build_id": cursor.build_id})

    try:
        return _enqueue()
    except db_store.InsufficientCompute as e:
        err = e
        released = db_store.abandon_pending_batch_jobs(
            run_id, "preempted: the build needs the remaining compute")
        if released:
            logger.warning("build %s: preempted %d queued asset job(s) to admit the next turn",
                           run_id, released)
            try:
                return _enqueue()
            except db_store.InsufficientCompute as e2:
                err = e2
        logger.error("build %s turn refused: %s", run_id, err)
        rs = RunState(run_id)
        cursor.phase = "done"
        cursor.ok = False
        build_state.save(rs.run_dir, cursor)
        db_store.set_status(run_id, "failed")
        if cursor.build_id:
            db_store.build_finished(cursor.build_id, "failed", steps=cursor.step)
        _emit("build_done", run_id, build_id=cursor.build_id, ok=False, steps=cursor.step,
              error=f"compute exhausted: {e}")


def _emit_step(run_id: str, cursor: BuildCursor, summary: str) -> None:
    _emit("build_step", run_id, build_id=cursor.build_id, step=cursor.step,
          max_steps=cursor.max_steps, summary=summary, elapsed=time.time() - cursor.t0)
    logger.info("build %s step %d: %s", run_id, cursor.step, summary)
