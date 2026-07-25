"""The build driver — what a finished build LLM turn does next.

A build is a linear chain of `llm`-queue jobs. Each job carries `metadata.stage="build"`; when a
worker completes it, `/worker/complete` routes here (the codegen analog of asset_chain). This module
owns the two-level state machine the old resident `AgentLoop` was:

  outer  — rebuild context from disk, sweep CodegenModule's gates, do the cross-fix stall/park
           bookkeeping, pick the most urgent error, and START a fix.
  fix    — a build_steps shape (plan/data/author/read_write); apply the completed turn's result and
           either enqueue the next turn or return to `outer`.

`advance()` runs all local work — gates, tool dispatch, deterministic fix passes — synchronously and
SUSPENDS only at a real inference: it enqueues one `llm` job and returns; the process is free to die.
The next completion reloads build_state.json and calls `advance` again. There is never more than one
build llm job in flight per run, so the chain is strictly linear and advance is only ever driven from
the control-plane process (the completion handler + the reaper) — an in-process lock serializes those.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Dict, List, Optional, Tuple

from db import store as db_store
from llm_clients.connector import get_connector
from maestro.codegen import build_state, build_steps
from maestro.codegen.build_state import BuildCursor, FixCursor, error_to_dict
from maestro.codegen.fix_classes import classify
from maestro.codegen import interfaces
from maestro.codegen.gates import stage_for_play
from maestro.codegen.module import CodegenModule
from maestro.codegen.tools import build_codegen_tools
from maestro.modules.context import build_context
from maestro.modules.module import Error, ErrorType, Module, idkey
from maestro.state import RunState
from tools.build_events import _emit

logger = logging.getLogger(__name__)

_TYPE_RANK = {ErrorType.HUMAN: 0, ErrorType.BUILD: 1, ErrorType.FIX: 2}
_STUCK_WINDOW = 40
_STUCK_REPEATS = 20
# How many sweeps an error may recur across before the CONTRACT gets a chance to be the thing that
# is wrong. Well under _STUCK_REPEATS: parking gives up on an error, amend still tries to fix it.
_AMEND_RECURRENCES = 3

# error.code -> fix shape (build_steps). Everything else is a read→edit subloop.
_SHAPE_BY_CODE = {"interfaced": "interfaces", "reviewed": "review", "data": "data",
                  "authored": "author", "conforms": "amend"}

# The spec-vs-code audit: after the gates go green, sweep the frozen spec's claims against the
# source and fix what isn't delivered — "done" means the spec is exhausted (or the caps are), never
# just errors-zero. Sweeps repeat until one returns ZERO findings (the spec-clean signal); the round
# cap is a backstop against a judge that never converges, not the intended exit. A human-note fix
# stays scoped to its note, so no audit there.
_AUDIT_KINDS = ("build", "audit")
_AUDIT_ROUNDS = 10

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


# ── pure helpers (ported from agent_loop) ─────────────────────────────────────
def collect_errors(module: Module, ctx) -> List[Tuple[Module, Error]]:
    return [(module, e) for e in module.get_errors(ctx)]


def prioritize(pairs: List[Tuple[Module, Error]]) -> Tuple[Module, Error]:
    return min(pairs, key=lambda p: (_TYPE_RANK[p[1].type], p[0].priority,
                                     p[0].check_rank(p[1].code), p[1].identity()))


def _todo_from_pairs(pairs: List[Tuple[Module, Error]]) -> List[dict]:
    return [{"component": e.component, "code": e.code, "type": e.type.value,
             "detail": e.message, "idkey": idkey(e), "path": e.path}
            for _, e in pairs]


# ── lifecycle ─────────────────────────────────────────────────────────────────
def kickoff(run_id: str, *, kind: str = "build", note: str = "", auto_pause: bool = False,
            max_steps: Optional[int] = None) -> str:
    """Create the build attempt row and start the build. Returns the build_id. The single entry the
    API and the CLI both call; `start_build` does the actual seeding + first advance."""
    if max_steps is None:
        max_steps = 40 if kind == "fix" else 200 if kind == "audit" else 60
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
    """The live build state for the status endpoints, or None when nothing is building. There is no
    build-level queue any more, so no position — a build's llm turns ride the shared `llm` queue."""
    cursor = build_state.load(RunState(run_id).run_dir)
    if cursor is None or cursor.phase == "done":
        return None
    return {"kind": cursor.kind}


def start_build(run_id: str, build_id: str, *, kind: str = "build", note: str = "",
                auto_pause: bool = False, max_steps: int = 60) -> None:
    """Kick a build off: seed the scaffolds, write the initial cursor, and advance once (which
    collects the gates, picks the first fix, and enqueues its first llm turn — then returns). A
    human-note fix seeds the cursor straight into a read→edit fix on the note, after which the outer
    loop re-gates and repairs any regression exactly as a build does."""
    from maestro.codegen.run import _seed   # local: run.py imports build_chain
    rs = RunState(run_id)
    spec = rs.read_spec()
    if spec is None:
        raise ValueError(f"no spec for run {run_id!r}")
    if kind in ("build", "audit") and not spec.get("frozen"):
        raise RuntimeError("build refuses to run until the spec is frozen")
    _seed(run_id, rs, spec)

    prev = build_state.load(rs.run_dir)
    cursor = BuildCursor(build_id=build_id, kind=kind, max_steps=max_steps, t0=time.time())
    if kind in _AUDIT_KINDS and prev is not None:
        # Carry the verdict anchors across builds: the prior build's delivered claims were judged
        # against this same on-disk code, so a fresh audit ratchets from them instead of
        # re-litigating from zero.
        cursor.audit_delivered = list(prev.audit_delivered)
    if kind == "fix":
        # The note is the failing gate; a synthetic HUMAN error classifies to `default` (grounded
        # hunk edits, no whole-file rewrite), exactly as the old fix_from_note.
        error = Error(type=ErrorType.HUMAN, code="human", component="game",
                      message=("HUMAN PLAYTEST FEEDBACK — the game passed the automated gates but is "
                               f"WRONG when a person plays it. Fix exactly this:\n{note}"))
        cursor.phase = "fix"
        cursor.prev = []   # so the post-fix bookkeeping runs when this fix returns to outer
        cursor.set_fix(FixCursor(shape="read_write", error=error_to_dict(error)))
    build_state.save(rs.run_dir, cursor)
    # Register the control so pause/auto-pause endpoints find this run (in-memory, like the old
    # build; it does not survive a restart, and resume re-drives from the durable cursor either way).
    from maestro.run_control import get_or_create
    get_or_create(run_id).set_auto_pause(auto_pause)
    db_store.set_status(run_id, "building")
    _emit("build_started", run_id, n_failing=0, max_steps=max_steps, todo=[], started_at=cursor.t0)
    # wait=True: a skin's re-gate build can kick off while the PREVIOUS build's finalize still
    # holds the run's advance lock — a non-blocking first advance would silently no-op and strand
    # the fresh cursor until the reaper's stuck-build sweep.
    advance(run_id, wait=True)


def on_completion(run_id: str, build_id: str, result: Optional[Dict], error: Optional[str]) -> None:
    """Drive the next step after a build llm turn lands. The result arrives in raw Responses shape off
    the queue — normalize it to chat before the fix shape applies it. A worker-reported error (or a
    lost result) becomes an empty turn the shape handles (retry/advance) rather than a stall."""
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
    tools = build_codegen_tools(rs, versions=cursor.tool_versions, seen=cursor.tool_seen)
    module = CodegenModule()

    while True:
        if control is not None and control.paused:
            control.set_status("paused")
            build_state.save(rs.run_dir, cursor)
            _emit("build_paused", run_id, step=cursor.step)
            return

        if cursor.phase == "outer":
            ctx = build_context(spec, rs)
            pairs = collect_errors(module, ctx)
            if cursor.prev is not None:
                _post_fix_bookkeeping(run_id, cursor, pairs)
            cursor.todo = _todo_from_pairs(pairs)
            if cursor.kind == "build" and cursor.asset_batch is None:
                _maybe_early_assets(run_id, rs, spec, cursor)
            if not pairs:
                if not _advance_audit(run_id, rs, cursor):
                    return   # finalized ok
                continue     # a finding's fix or an audit sweep is armed — run its first turn
            # The step cap bounds the NUMBER of fixes, checked here (after the clean-check, so a fix
            # that greens everything exactly at the cap still finalizes ok). Each fix is itself
            # bounded by its per-shape turn cap, so the fix branch below needs no cap check.
            if cursor.step >= cursor.max_steps:
                _finalize(run_id, rs, cursor, ok=False)
                return
            active = [(m, e) for m, e in pairs if idkey(e) not in cursor.parked]
            if not active:
                _finalize(run_id, rs, cursor, ok=False)
                return
            snap = build_state.snapshot(pairs)
            stalled = cursor.prev is not None and set(cursor.prev) == set(snap)
            _, error = prioritize(active)
            cursor.prev = snap
            started = _start_fix(run_id, rs, cursor, error, stalled)
            if not started:
                # A deterministic pass resolved it — no llm turn, no step consumed: steps meter the
                # MODEL's budget. Unbounded looping is prevented by identity, not by the cap — each
                # error identity gets exactly one free pass (det_tried), so a pass that doesn't
                # actually clear its error goes to the LLM on the next encounter.
                continue
            # phase is now "fix"; fall through with result=None

        # phase == "fix"
        fc = cursor.fix_cursor()
        # result is None when the reaper (or resume) re-drives a build whose completion was lost:
        # the fix shape applies an EMPTY turn (retry) rather than crashing on None, exactly as
        # on_completion coerces a lost/errored result to {}.
        outcome = build_steps.step(fc.shape, spec, rs.run_dir, tools, fc, result or {})
        result = None
        cursor.set_fix(fc)
        if isinstance(outcome, build_steps.Infer):
            cursor.step += 1
            build_state.save(rs.run_dir, cursor)
            _enqueue_turn(run_id, cursor, outcome)
            if cursor.phase != "done":   # a refused budget finalizes inside _enqueue_turn
                _emit_step(run_id, cursor, outcome.report)
            return
        # Done: the fix finished — back to the outer gate sweep. A finished audit sweep hands its
        # findings to the pending queue; the outer loop fixes them one per iteration (re-gating
        # between, so a fix that regresses a gate is repaired before the next finding runs).
        if fc.shape == "audit":
            cursor.audit_pending = list(fc.findings or [])
            cursor.audit_done = not cursor.audit_pending
            cursor.audit_delivered = list(fc.delivered or [])
        cursor.set_fix(None)
        cursor.phase = "outer"
        build_state.save(rs.run_dir, cursor)
        _emit_step(run_id, cursor, outcome.report)


def _advance_audit(run_id: str, rs: RunState, cursor: BuildCursor) -> bool:
    """The gates are green — decide what greenness means. Arm the next pending finding's fix, else
    the next audit sweep, and return True; when the audit is exhausted (clean sweep, round cap, step
    cap, or a kind that doesn't audit) finalize ok and return False. Every limit here FAILS OPEN to
    a successful build: an incomplete audit ships the game, it never strands it."""
    if cursor.audit_pending and cursor.step < cursor.max_steps:
        finding = cursor.audit_pending.pop(0)
        error = Error(type=ErrorType.HUMAN, code="audit", component="game",
                      message=finding["note"])
        cursor.prev = []
        cursor.set_fix(FixCursor(shape="read_write", error=error_to_dict(error)))
        cursor.phase = "fix"
        return True
    if (cursor.kind in _AUDIT_KINDS and not cursor.audit_done
            and cursor.audit_round < _AUDIT_ROUNDS and cursor.step < cursor.max_steps):
        cursor.audit_round += 1
        sweep = Error(type=ErrorType.BUILD, code="audit_sweep", component="game",
                      message=f"spec-vs-code audit (round {cursor.audit_round}/{_AUDIT_ROUNDS})")
        cursor.prev = []
        cursor.set_fix(FixCursor(shape="audit", error=error_to_dict(sweep),
                                 anchors=list(cursor.audit_delivered)))
        cursor.phase = "fix"
        return True
    _finalize(run_id, rs, cursor, ok=True)
    return False


def _maybe_early_assets(run_id: str, rs: RunState, spec: dict, cursor: BuildCursor) -> None:
    """The EARLY asset lane: once the data rows land, their `look` prompts are the whole render
    plan — start the GPU on sprites/meshes while the llm turns keep building, so a finished game
    is playable WITH its art instead of shapes-first. Wiring/staging stay the finalize's job."""
    from maestro.codegen import reskin
    try:
        batch = reskin.start_assets_early(run_id, rs.run_dir, spec)
    except Exception:
        logger.exception("early asset lane failed for %s", run_id)
        batch = ""   # never retry a lane that throws — the green lane still covers the game
    if batch is not None:
        cursor.asset_batch = batch
        build_state.save(rs.run_dir, cursor)


def _start_fix(run_id: str, rs: RunState, cursor: BuildCursor, error: Error, stalled: bool) -> bool:
    """Enter the fix for `error`: pick its shape, and for a read→edit fix run the fix class's
    DETERMINISTIC pre-pass first. If that pass changes files, the fix is resolved with no llm turn
    (returns False, stays in `outer`). A read→edit fix that has already stalled is routed through
    `amend` once, so the contract gets a chance to be the thing that is wrong. Otherwise arm the fix
    cursor and switch to `fix` (True)."""
    shape = _SHAPE_BY_CODE.get(error.code, "read_write")
    if shape == "read_write":
        cls = classify(error)
        if cls.deterministic is not None and idkey(error) not in cursor.det_tried:
            res = cls.deterministic(rs.run_dir, error) or {}
            if res.get("count"):
                cursor.det_tried.append(idkey(error))
                summary = ", ".join(
                    f"{k}:{v if isinstance(v, str) else '.'.join(map(str, v[:2]))}"
                    for k, v in res.get("changes", [])[:8])
                _emit_step(run_id, cursor, f"[{cls.id}] deterministic pass ({res['count']} edit(s): {summary})")
                return False
        # RECURRENCE, not just a repeated snapshot: two errors that each re-cause the other
        # oscillate, so the to-do changes every sweep and `stalled` never trips — which is exactly
        # the shape a wrong contract makes, because neither file is the one that is wrong.
        # Counted over errors this build actually TRIED to fix, never over the to-do: a sweep's
        # snapshot lists every failing error, so an error merely waiting its turn behind higher
        # priority ones would otherwise earn a contract ruling before one line of it was ever edited.
        cursor.attempted.append(idkey(error))
        recurred = cursor.attempted.count(idkey(error)) >= _AMEND_RECURRENCES
        if ((stalled or recurred) and idkey(error) not in cursor.amend_tried
                and interfaces.load(rs.run_dir)):
            # A code fix that made no progress may be unfixable in the code: the architecture was
            # declared before any of it existed, and a file authored faithfully to a wrong contract
            # is the file the gate blames. `amend` is the only shape that can rule the CONTRACT
            # wrong; without this it fires on conformance alone, so every other gate's fix loop
            # grinds against a declaration it is not allowed to contradict. One ruling per error
            # identity — a `code` verdict falls straight through to the read→edit subloop, so a
            # wrong guess costs one turn.
            cursor.amend_tried.append(idkey(error))
            shape = "amend"
    cursor.set_fix(FixCursor(shape=shape, error=error_to_dict(error), escalate=stalled))
    cursor.phase = "fix"
    return True


def _post_fix_bookkeeping(run_id: str, cursor: BuildCursor, pairs) -> None:
    """After a fix returns to outer: record the to-do snapshot, and if the SAME snapshot has recurred
    across the window, PARK the top error so the loop moves on (ported from AgentLoop's stuck
    detector). Parking is paced — one per stuck stretch, then the window resets."""
    snap = build_state.snapshot(pairs)
    cursor.recent.append(snap)
    if len(cursor.recent) > _STUCK_WINDOW:
        cursor.recent = cursor.recent[-_STUCK_WINDOW:]
    if snap and cursor.recent.count(snap) >= _STUCK_REPEATS:
        spinning = [(m, e) for m, e in pairs if idkey(e) not in cursor.parked]
        if spinning:
            _, e = prioritize(spinning)
            cursor.parked.append(idkey(e))
            _emit("error_parked", run_id, identity=list(e.identity()), message=e.message)
            logger.info("parked (build spun): [%s] %s: %s", e.component, e.code, e.message[:120])
        cursor.recent = []


def _finalize(run_id: str, rs: RunState, cursor: BuildCursor, ok: bool) -> None:
    cursor.phase = "done"
    cursor.ok = ok
    build_state.save(rs.run_dir, cursor)
    if ok:
        _absorb_assets(rs.run_dir)
        stage_for_play(rs.run_dir, run_id)
    db_store.set_status(run_id, "built" if ok else "failed")
    if cursor.build_id:
        db_store.build_finished(cursor.build_id, "succeeded" if ok else "failed", steps=cursor.step)
    from maestro.run_control import remove as remove_control
    remove_control(run_id)
    logger.info("build %s finalized: ok=%s steps=%d", run_id, ok, cursor.step)
    _emit("build_done", run_id, ok=ok, steps=cursor.step)
    if ok:
        threading.Thread(target=_auto_skin, args=(run_id, cursor.asset_batch or None),
                         daemon=True, name=f"autoskin-{run_id}").start()


def _absorb_assets(run_dir) -> None:
    """Meshes an early batch landed DURING the build: their box-fit + rebundle was deferred (the
    asset finalize won't rewrite a building world mid-build) — pick them up before staging."""
    from maestro.codegen.gates import build_bundle
    from maestro.codegen.reskin import fit_building_boxes
    if fit_building_boxes(run_dir):
        build_bundle(run_dir)


def _auto_skin(run_id: str, early_batch: Optional[str]) -> None:
    """The GREEN asset lane, fired after every ok finalize: make the assets exist and be wired
    without a click (reskin.auto_skin no-ops when there is nothing left to do)."""
    from maestro.codegen import reskin
    try:
        reskin.auto_skin(run_id, early_batch=early_batch)
    except Exception:
        logger.exception("auto-skin failed for %s", run_id)


# ── enqueue + events ──────────────────────────────────────────────────────────
def _enqueue_turn(run_id: str, cursor: BuildCursor, inf: "build_steps.Infer") -> None:
    """Land one build llm turn as a pending `llm` job. Its completion re-enters advance. A refused
    compute budget first PREEMPTS the run's queued (unclaimed) asset renders — gameplay beats skin,
    and the early lane is opportunistic by design — then, still refused, ends the build: a broke
    run can't spin on refused turns."""
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
          n_failing=len(cursor.todo), todo=cursor.todo, elapsed=time.time() - cursor.t0)
    logger.info("build %s step %d: %s", run_id, cursor.step, summary)
