"""agent_loop — the non-LLM executor. Drives the build until every module's errors clear.

The loop is now just: collect each module's errors, subtract the human's waivers, pick the most
urgent (error TYPE human>build>fix, then Module.priority), ask that module for a Fix, and run it
through a per-fix `Services` budget. The module owns the SHAPE of the fix (one step, or its own
iterative author loop); `Services` owns the LIMITS (checkpoint + budget). The loop keeps the two
guarantees: completion (`effective_errors == []`, never the agent's say-so) and cross-fix stall
detection (the same top error after a full fix attempt → start the next fix escalated).
"""

import logging
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from llm_clients.connector_selector import get_connector
from maestro.modules.context import build_context
from maestro.modules.module import Error, ErrorType, Module, idkey
from maestro.services import BudgetExhausted, Services

logger = logging.getLogger(__name__)

_TYPE_RANK = {ErrorType.HUMAN: 0, ErrorType.BUILD: 1, ErrorType.FIX: 2}
_FIX_CAP = 25   # the most steps any one fix may spend (the loop also clamps to the global remaining)
# Stuck detection: keep the last N error-list SNAPSHOTS; if the SAME snapshot recurs this many
# times the build is spinning — give up on its top error so the rest can proceed. Snapshotting the
# error SET (not its size) is robust two ways a count is not: it catches A/B oscillation (both sets
# recur), and it does NOT false-park authoring that spawns downstream demand (a beat add removes a
# min_beats slot but adds a scene slot — the count stays flat while the SET changes every step).
_STUCK_WINDOW = 40
_STUCK_REPEATS = 20


@dataclass
class LoopResult:
    ok: bool
    steps: int
    failures: List[Error] = field(default_factory=list)
    elapsed: float = 0.0


# ── error collection + prioritization ────────────────────────────────────────
def collect_errors(modules: List[Module], context) -> List[Tuple[Module, Error]]:
    out: List[Tuple[Module, Error]] = []
    for m in modules:
        for e in m.get_errors(context):
            out.append((m, e))
    return out

def effective_pairs(modules: List[Module], context) -> List[Tuple[Module, Error]]:
    return [(m, e) for m, e in collect_errors(modules, context)]


def prioritize(pairs: List[Tuple[Module, Error]]) -> Tuple[Module, Error]:
    return min(pairs, key=lambda p: (_TYPE_RANK[p[1].type], p[0].priority,
                                     p[0].check_rank(p[1].code), p[1].identity()))


def _same(a, b) -> bool:
    return {e.identity() for _, e in a} == {e.identity() for _, e in b}


def _todo_from_pairs(pairs: List[Tuple[Module, Error]]) -> List[dict]:
    """The effective to-do serialized the same way `human.effective_failures` renders it for the
    detail endpoint — so `build_started`/`build_step` carry the live board instead of the frontend
    reading a `todo` field the backend never set."""
    return [{"component": e.component, "code": e.code, "type": e.type.value,
             "detail": e.message, "idkey": idkey(e), "path": e.path}
            for _, e in pairs]


class AgentLoop:
    """Drive a frozen spec's modules to completion.

    modules : the composed list (human first).
    tools   : name -> callable(**args) -> dict.
    """

    def __init__(self, spec: dict, state, modules: List[Module], tools: dict, *, connector=None,
                 max_steps: int = 1000, on_event=None, on_milestone=None, control=None,
                 parallel: int = 1):
        self.spec = spec
        self.state = state
        self.modules = modules
        self.tools = tools
        self.conn = connector or get_connector()
        self.max_steps = max_steps
        self.on_event = on_event
        self.on_milestone = on_milestone
        self.control = control
        self.parallel = max(1, parallel)
        self._dispatch_lock = threading.Lock()   # serializes tool writes across parallel fixes
        self._step_lock = threading.Lock()       # serializes the step counter + progress events
        self.last_result: Optional[str] = None
        self.last_read: Optional[str] = None

    def _context(self, stalled: bool = False):
        ctx = build_context(self.spec, self.state, last_result=self.last_result,
                            last_read=self.last_read, stalled=stalled)
        pairs = effective_pairs(self.modules, ctx)
        ctx.errors = [e for _, e in pairs]
        return ctx, pairs

    def _emit(self, event_type: str, **fields) -> None:
        if self.on_event is None:
            return
        try:
            self.on_event({"type": event_type, **fields})
        except Exception:
            logger.exception("on_event callback failed for %s", event_type)

    def _fire_milestone(self, component_id: str) -> None:
        if self.control is not None and self.control.auto_pause:
            self.control.request_pause()
            self._emit("auto_paused", component_id=component_id)
        if self.on_milestone is None:
            return
        try:
            self.on_milestone(component_id)
        except Exception:
            logger.exception("on_milestone callback failed for %s", component_id)

    def _checkpoint(self, step: int) -> None:
        c = self.control
        if c is None:
            return
        if c.paused:
            c.set_status("paused")
            self._emit("build_paused", step=step)
            c.wait_while_paused()
            c.set_status("running")
            self._emit("build_resumed", step=step)

    def _on_step(self, summary: str) -> None:
        """The per-action progress sink Services calls. The loop owns the running step count + the
        last-known failing count, so it's the single emitter of build_step (per LLM call). Locked:
        parallel fixes report from their own threads. `todo` is the to-do as of the last full
        recollection (start of this step's batch) — a per-call recompute would rebuild context on
        every single LLM call, so it's refreshed once per loop iteration, not mid-batch."""
        with self._step_lock:
            self.step += 1
            step = self.step
            self.last_result = summary
            todo = _todo_from_pairs(self._pairs)
            self._emit("build_step", step=step, max_steps=self.max_steps,
                       summary=summary, n_failing=self._n_failing,
                       todo=todo,
                       elapsed=time.time() - self._t0)
        print(f"  step {step}: {summary}", flush=True)
        if todo:
            first = (todo[0].get("detail") or "").split("\n")[0][:150]
            print(f"          fixing [{todo[0].get('code')}] {first}", flush=True)

    def _batch(self, ctx, pairs, module: Module, error: Error) -> List[Tuple[Module, Error, int]]:
        """The fixes to run this step: the top error alone, or — when its check is a slot-guarded
        create — up to `parallel` same-code siblings, each with its own slot index. Only guarded
        creates batch: they are independent by construction (distinct owed slots); everything else
        (edits, crossref, human notes) may touch the same target, so it stays serial. A guard may
        declare its own `cap(view)` (scenes: one per open slot, one on an empty graph) — the loop
        knows no module's view shape."""
        chk = module._check_for(error.code)
        if self.parallel <= 1 or chk is None or chk.guard is None:
            return [(module, error, 0)]
        cap = self.parallel
        cap_fn = chk.guard.get("cap")
        if cap_fn:
            cap = min(cap, max(1, cap_fn(module.view(ctx.artifact) or {})))
        group = sorted((e for m, e in pairs if m is module and e.code == error.code),
                       key=lambda e: e.identity())
        return [(module, e, i) for i, e in enumerate(group[:cap])]

    def _run_fixes(self, ctx, batch: List[Tuple[Module, Error, int]], stalled: bool) -> int:
        """Run the batch — one thread per fix (LLM calls overlap; tool dispatch serializes on the
        loop's lock). Returns the total steps spent."""
        # The global remainder is shared across the batch — each worker gets its share, so N
        # workers can't jointly overshoot max_steps by N× the remainder.
        budget = min(_FIX_CAP, max(1, (self.max_steps - self.step) // max(1, len(batch))))

        def one(module: Module, error: Error, slot: int) -> Services:
            services = Services(self.conn, self.tools, self.spec, self.state, budget=budget,
                                control=self.control, on_event=self.on_event,
                                report=self._on_step, escalate=stalled,
                                lock=self._dispatch_lock if len(batch) > 1 else None)
            try:
                module.get_fix(ctx, error, slot=slot)(services)
            except BudgetExhausted:
                pass   # the fix used its whole budget; recollect and move on
            return services

        if len(batch) == 1:
            done = [one(*batch[0])]
        else:
            with ThreadPoolExecutor(max_workers=len(batch)) as ex:
                done = [f.result() for f in [ex.submit(one, m, e, s) for m, e, s in batch]]
        self.last_read = next((s.last_read for s in reversed(done) if s.last_read), None)
        return sum(s.spent for s in done)

    def run(self) -> LoopResult:
        if not self.spec.get("frozen"):
            raise RuntimeError("build refuses to run until the spec is frozen")
        ctx, pairs = self._context()
        self._pairs = pairs   # the live to-do _on_step reads for build_step's `todo`
        all_components = {cid for m in self.modules for cid in m.affected_components()}
        passed = all_components - {e.component for _, e in pairs}
        self.step = 0
        self._n_failing = len(pairs)
        self._parked: set = set()                     # error identities the loop has given up on
        self._recent = deque(maxlen=_STUCK_WINDOW)    # the last N error-list snapshots
        self._t0 = time.time()
        self._emit("build_started", n_failing=len(pairs), max_steps=self.max_steps,
                   todo=_todo_from_pairs(pairs), started_at=self._t0)
        prev: Optional[List] = None
        while self.step < self.max_steps:
            self._checkpoint(self.step)
            if not pairs:
                self._emit("build_done", ok=True, steps=self.step)
                return LoopResult(ok=True, steps=self.step)
            # Parked errors need a human; every further step on them starves the rest of the
            # build. Parking is decided below by the stuck detector, not a per-error counter.
            active = [(m, e) for m, e in pairs if e.identity() not in self._parked]
            if not active:
                self._emit("build_done", ok=False, steps=self.step)
                return LoopResult(ok=False, steps=self.step, failures=[e for _, e in pairs])
            stalled = prev is not None and _same(prev, pairs)
            module, error = prioritize(active)
            batch = self._batch(ctx, active, module, error)
            if self._run_fixes(ctx, batch, stalled) == 0:
                self.step += 1   # a batch that made no LLM call still advances, so we can't spin

            prev = pairs
            ctx, pairs = self._context()
            self._pairs = pairs
            self._n_failing = len(pairs)
            # The build is spinning if the SAME error list keeps recurring across the window
            # (count catches oscillation; a changing SET means real progress even when the size
            # holds). Give up on the current top error so the loop moves on, then reset the
            # window so parking is PACED (one per stuck stretch), never a cascade.
            snap = frozenset(e.identity() for _, e in pairs)
            self._recent.append(snap)
            if snap and self._recent.count(snap) >= _STUCK_REPEATS:
                spinning = [(m, e) for m, e in pairs if e.identity() not in self._parked]
                if spinning:
                    _m, e = prioritize(spinning)
                    self._parked.add(e.identity())
                    self._emit("error_parked", identity=list(e.identity()), message=e.message)
                    print(f"  parked (build spun on the same error list "
                          f"{self._recent.count(snap)}/{len(self._recent)} steps): "
                          f"[{e.component}] {e.code}: {e.message[:120]}", flush=True)
                self._recent.clear()
            now_failing = {e.component for _, e in pairs}
            for cid in (all_components - now_failing) - passed:
                self._fire_milestone(cid)
            passed = all_components - now_failing
        self._emit("build_done", ok=not pairs, steps=self.step)
        return LoopResult(ok=not pairs, steps=self.step, failures=[e for _, e in pairs])
