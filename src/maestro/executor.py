"""The executor — drives the build loop. NOT an LLM.

Steals the pipeline's two good properties without its rigidity:
  - completion guarantee: done = the artifact satisfies the frozen spec
    (validate's failure list is empty), NOT the agent claiming it's done.
  - no context rot: each step rebuilds a fresh minimal context from durable
    state (spec + validate to-do + last-result + scratchpad). The transcript is
    never used as memory.

The agent (an LLM in production, a scripted decider in tests) only *chooses the
next action*. The executor builds context, dispatches the chosen tool, and
recomputes the to-do. The growing artifact lives outside the context window, so
context stays roughly constant as the game grows.
"""

import logging
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

from maestro.validate import validate

logger = logging.getLogger(__name__)


@dataclass
class StepRecord:
    step: int
    action: Dict
    summary: str


@dataclass
class ExecutorResult:
    ok: bool
    steps: int
    failures: List[Dict] = field(default_factory=list)
    history: List[StepRecord] = field(default_factory=list)
    elapsed: float = 0.0


class SpecNotFrozenError(RuntimeError):
    pass


class Executor:
    """
    tools:  name -> callable(**args) -> result (dict). The artifact/spec tools.
    decide: callable(context) -> {"tool": str, "args": dict}. The agent's choice.
    """

    def __init__(self, spec, state, tools: Dict[str, Callable], decide: Callable,
                 max_steps: int = 60, on_milestone: Optional[Callable[[str], None]] = None,
                 on_event: Optional[Callable[[Dict], None]] = None,
                 projectors: Optional[Dict[str, Callable]] = None):
        self.spec = spec
        self.state = state
        self.tools = tools
        self.decide = decide
        self.max_steps = max_steps
        # component_id -> fn(artifact)->dict: a compact view of the ACTIVE component handed
        # to the agent (e.g. the node graph for node_scripts). Injected by the caller so
        # maestro stays genre-agnostic. None = no view.
        self.projectors = projectors or {}
        # Architecture-triggered check-in: called the first time a component's
        # done-conditions all pass. The agent doesn't judge when to interrupt.
        self.on_milestone = on_milestone
        # Optional progress sink (e.g. the websocket bus). Transport-agnostic: the
        # executor emits structured dicts; the caller decides where they go.
        self.on_event = on_event
        self.last_result: Optional[str] = None

    def _emit(self, event_type: str, **fields) -> None:
        if self.on_event is None:
            return
        try:
            self.on_event({"type": event_type, **fields})
        except Exception:
            logger.exception("on_event callback failed for %s", event_type)

    # ── context (rebuilt fresh each step from durable state) ─────────────────
    def build_context(self, todo: Optional[List[Dict]] = None) -> Dict:
        # `todo` may be passed in to avoid recomputing validate (the compiles check
        # runs a real Ren'Py build, so we run validate once per step, not per use).
        todo = validate(self.spec, self.state) if todo is None else todo

        # A component whose done-conditions all pass is "locked": its content (e.g. the
        # premise's character ids) is settled and downstream steps must conform to it, so
        # hand it to the agent directly instead of making it call read_component. Derived
        # from the to-do — no extra validate. node_scripts is compiles-gated, so it only
        # passes when the build is essentially done; it never bloats this mid-build.
        failing_ids = {f["component_id"] for f in todo}
        upstream = {}
        for cid in (c.get("id") for c in self.spec.components):
            if cid in failing_ids:
                continue
            content = self.state.read_component(cid)
            if content is not None:
                upstream[cid] = content

        # The active "mode" = the earliest still-failing component in dependency order.
        # The executor decides this, not the agent (routing stays non-LLM).
        mode = next((cid for cid in self.spec.dep_order() if cid in failing_ids), None)

        # A compact view of the active component (e.g. node_scripts' graph) so the agent
        # sees what already exists and fixes it surgically instead of rewriting blind.
        active_view = None
        projector = self.projectors.get(mode)
        if projector is not None:
            active_view = projector(self.state.load_artifact())

        return {
            "spec": {
                "title": self.spec.title,
                "request": self.spec.request,
                "components": [
                    {"id": c.get("id"), "description": c.get("description", ""),
                     "deps": c.get("deps", []), "done_conditions": c.get("done_conditions", [])}
                    for c in self.spec.components
                ],
            },
            "todo": todo,
            "mode": mode,
            "upstream": upstream,
            "active_view": active_view,
            "scratchpad": self.state.read_scratchpad(),
            "story_state": self.state.read_story_state(),
            "last_result": self.last_result,
            "available_tools": sorted(self.tools),
        }

    # ── completion: decided by validate, never by the agent ──────────────────
    def _failing_components(self) -> set:
        return {f["component_id"] for f in validate(self.spec, self.state)}

    def is_done(self) -> bool:
        return self.spec.frozen and not validate(self.spec, self.state)

    def run(self) -> ExecutorResult:
        if not self.spec.frozen:
            raise SpecNotFrozenError("build refuses to run until the spec is frozen")

        history: List[StepRecord] = []
        all_ids = {c.get("id") for c in self.spec.components}
        # A component not in the failing set already passes; only fire milestones
        # for ones that were failing and then transition to passing.
        failures = validate(self.spec, self.state)
        passed = all_ids - {f["component_id"] for f in failures}
        self._emit("build_started", n_failing=len(failures), max_steps=self.max_steps,
                   todo=failures)

        for step in range(1, self.max_steps + 1):
            if not failures:                       # frozen already checked above
                self._emit("build_done", ok=True, steps=step - 1)
                return ExecutorResult(ok=True, steps=step - 1, history=history)

            # Log before the (slow) LLM call so the loop isn't a silent black box.
            print(f"  step {step}/{self.max_steps}: {len(failures)} check(s) failing — deciding...",
                  flush=True)
            t0 = time.perf_counter()
            ctx = self.build_context(todo=failures)
            mode = ctx.get("mode")
            action = self.decide(ctx) or {}
            result = self._dispatch(action)
            self.last_result = self._summarize(action, result)
            history.append(StepRecord(step=step, action=action, summary=self.last_result))

            # One validate per step (it runs the compiles build); reused next iteration.
            failures = validate(self.spec, self.state)
            now_failing = {f["component_id"] for f in failures}
            for cid in (all_ids - now_failing) - passed:
                self._fire_milestone(cid)
            passed = all_ids - now_failing

            dt = time.perf_counter() - t0
            print(f"  step {step}: {self.last_result}  [{dt:.0f}s, {len(failures)} failing]",
                  flush=True)
            self._emit("build_step", step=step, max_steps=self.max_steps, mode=mode,
                       summary=self.last_result, elapsed=round(dt, 1), n_failing=len(failures),
                       todo=failures)

        self._emit("build_done", ok=not failures, steps=self.max_steps)
        return ExecutorResult(ok=not failures, steps=self.max_steps,
                              failures=failures, history=history)

    def _fire_milestone(self, component_id: str) -> None:
        if self.on_milestone is None:
            return
        try:
            self.on_milestone(component_id)
        except Exception:
            logger.exception("on_milestone callback failed for %s", component_id)

    def _dispatch(self, action: Dict) -> Dict:
        name = action.get("tool")
        fn = self.tools.get(name)
        if fn is None:
            return {"error": f"unknown tool: {name!r}"}
        try:
            return fn(**action.get("args", {}))
        except Exception as e:
            logger.exception("tool %s failed", name)
            return {"error": f"{name} failed: {e}"}

    def _summarize(self, action: Dict, result: Dict) -> str:
        name = action.get("tool", "?")
        args = action.get("args", {}) if isinstance(action.get("args"), dict) else {}
        target = args.get("node_id") or args.get("component_id") or ""
        label = f"{name}({target})" if target else name
        if isinstance(result, dict) and result.get("error"):
            return f"{label}: error — {result['error']}"
        return f"{label}: ok"
