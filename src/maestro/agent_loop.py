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
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from maestro.modules import human as human_mod
from maestro.modules.context import build_context
from maestro.modules.module import Error, ErrorType, Module, idkey
from maestro.services import BudgetExhausted, Services
from maestro.run_control import BuildCancelled

logger = logging.getLogger(__name__)

_TYPE_RANK = {ErrorType.HUMAN: 0, ErrorType.BUILD: 1, ErrorType.FIX: 2}
_FIX_CAP = 25   # the most steps any one fix may spend (the loop also clamps to the global remaining)


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
    waived = human_mod.waived_idkeys(context.state)
    return [(m, e) for m, e in collect_errors(modules, context) if idkey(e) not in waived]


def prioritize(pairs: List[Tuple[Module, Error]]) -> Tuple[Module, Error]:
    return min(pairs, key=lambda p: (_TYPE_RANK[p[1].type], p[0].priority,
                                     p[0].check_rank(p[1].code), p[1].identity()))


def _same(a, b) -> bool:
    return {e.identity() for _, e in a} == {e.identity() for _, e in b}


class AgentLoop:
    """Drive a frozen spec's modules to completion.

    modules : the composed list (human first).
    tools   : name -> callable(**args) -> dict.
    """

    def __init__(self, spec: dict, state, modules: List[Module], tools: dict, *, connector=None,
                 max_steps: int = 300, on_event=None, on_milestone=None, control=None,
                 parallel: int = 1):
        from llm_clients.connector_selector import get_connector
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
        self.upstream_views = {cid: m.context_view for m in modules
                               if hasattr(m, "context_view") for cid in m.affected_components()}

    def _context(self, stalled: bool = False):
        ctx = build_context(self.spec, self.state, last_result=self.last_result,
                            last_read=self.last_read, stalled=stalled)
        ctx.upstream_views = self.upstream_views
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
        if c.cancelled:
            raise BuildCancelled()
        if c.paused:
            c.set_status("paused")
            self._emit("build_paused", step=step)
            c.wait_while_paused()
            if c.cancelled:
                raise BuildCancelled()
            c.set_status("running")
            self._emit("build_resumed", step=step)

    def _on_step(self, summary: str) -> None:
        """The per-action progress sink Services calls. The loop owns the running step count + the
        last-known failing count, so it's the single emitter of build_step (per LLM call). Locked:
        parallel fixes report from their own threads."""
        with self._step_lock:
            self.step += 1
            step = self.step
            self.last_result = summary
            self._emit("build_step", step=step, max_steps=self.max_steps,
                       summary=summary, n_failing=self._n_failing)
        print(f"  step {step}: {summary}", flush=True)

    def _batch(self, ctx, pairs, module: Module, error: Error) -> List[Tuple[Module, Error, int]]:
        """The fixes to run this step: the top error alone, or — when its check is a slot-guarded
        create — up to `parallel` same-code siblings, each with its own slot index. Only guarded
        creates batch: they are independent by construction (distinct owed slots); everything else
        (edits, crossref, human notes) may touch the same target, so it stays serial. A view that
        publishes `open_slots` (nodes) caps the batch at the open slots actually available — a
        worker without a real slot would free-write an orphan."""
        chk = module._check_for(error.code)
        if self.parallel <= 1 or chk is None or chk.guard is None:
            return [(module, error, 0)]
        cap = self.parallel
        view = module.view(ctx.artifact) or {}
        if view.get("open_slots") is not None:
            # Empty graph: exactly one worker writes the opening node (parallel roots would each
            # free-choose an id — a forest, not a story).
            cap = min(cap, max(1, len(view["open_slots"])) if view.get("node_ids") else 1)
        group = sorted((e for m, e in pairs if m is module and e.code == error.code),
                       key=lambda e: e.identity())
        return [(module, e, i) for i, e in enumerate(group[:cap])]

    def _run_fixes(self, ctx, batch: List[Tuple[Module, Error, int]], stalled: bool) -> int:
        """Run the batch — one thread per fix (LLM calls overlap; tool dispatch serializes on the
        loop's lock). Returns the total steps spent. BuildCancelled from any worker re-raises after
        the others finish (they see the same cancel at their next checkpoint)."""
        budget = min(_FIX_CAP, self.max_steps - self.step)

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
            cancelled: Optional[BuildCancelled] = None
            done = []
            with ThreadPoolExecutor(max_workers=len(batch)) as ex:
                for f in [ex.submit(one, m, e, s) for m, e, s in batch]:
                    try:
                        done.append(f.result())
                    except BuildCancelled as exc:
                        cancelled = exc
            if cancelled is not None:
                raise cancelled
        self.last_read = next((s.last_read for s in reversed(done) if s.last_read), None)
        return sum(s.spent for s in done)

    def run(self) -> LoopResult:
        if not self.spec.get("frozen"):
            raise RuntimeError("build refuses to run until the spec is frozen")
        ctx, pairs = self._context()
        all_components = {cid for m in self.modules for cid in m.affected_components()}
        passed = all_components - {e.component for _, e in pairs}
        self.step = 0
        self._n_failing = len(pairs)
        self._emit("build_started", n_failing=len(pairs), max_steps=self.max_steps)
        prev: Optional[List] = None
        while self.step < self.max_steps:
            try:
                self._checkpoint(self.step)
                if not pairs:
                    self._emit("build_done", ok=True, steps=self.step)
                    return LoopResult(ok=True, steps=self.step)
                stalled = prev is not None and _same(prev, pairs)
                module, error = prioritize(pairs)
                batch = self._batch(ctx, pairs, module, error)
                if self._run_fixes(ctx, batch, stalled) == 0:
                    self.step += 1   # a batch that made no LLM call still advances, so we can't spin

                prev = pairs
                ctx, pairs = self._context()
                self._n_failing = len(pairs)
                now_failing = {e.component for _, e in pairs}
                for cid in (all_components - now_failing) - passed:
                    self._fire_milestone(cid)
                passed = all_components - now_failing
            except BuildCancelled:
                if self.control is not None:
                    self.control.set_status("cancelled")
                self._emit("build_cancelled", steps=self.step)
                return LoopResult(ok=False, steps=self.step, failures=[e for _, e in pairs])
        self._emit("build_done", ok=not pairs, steps=self.step)
        return LoopResult(ok=not pairs, steps=self.step, failures=[e for _, e in pairs])
