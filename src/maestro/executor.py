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

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

from maestro.validate import validate, run_check

logger = logging.getLogger(__name__)

# Reads carry no artifact change; repeating one is the agent spinning, not progressing.
_READ_TOOLS = {"read_node", "read_component", "read_story_state"}

# Order a sub-loop attacks failing checks: a broken script poisons everything, so fix
# syntax first; then reach the node count; then structure; then content quality.
_CHECK_PRIORITY = {
    "compiles": 0,
    "count": 1,
    "reachable_from_start": 2, "min_branches": 2, "refs_resolve": 2, "distinct": 2, "exists": 2,
    "each_node_min_lines": 3, "all_characters_speak": 3, "each_has": 3,
}


def pick_target(failures: List[Dict]) -> Dict:
    """The single failing check a sub-loop should drive to green next."""
    return min(failures, key=lambda f: _CHECK_PRIORITY.get(f.get("check", {}).get("type"), 2))


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
                 projectors: Optional[Dict[str, Callable]] = None,
                 sub_runners: Optional[Dict[str, Callable]] = None):
        self.spec = spec
        self.state = state
        self.tools = tools
        self.decide = decide
        self.max_steps = max_steps
        # component_id -> fn(artifact)->dict: a compact view of the ACTIVE component handed
        # to the agent (e.g. the node graph for node_scripts). Injected by the caller so
        # maestro stays genre-agnostic. None = no view.
        self.projectors = projectors or {}
        # component_id -> stateful runner(target, context, dispatch, target_met, report,
        # budget, view_fn) -> steps_used. For components that need iterate-until-done
        # (node_scripts), a sub-loop with its own working memory replaces one-shot steps.
        self.sub_runners = sub_runners or {}
        # Architecture-triggered check-in: called the first time a component's
        # done-conditions all pass. The agent doesn't judge when to interrupt.
        self.on_milestone = on_milestone
        # Optional progress sink (e.g. the websocket bus). Transport-agnostic: the
        # executor emits structured dicts; the caller decides where they go.
        self.on_event = on_event
        self.last_result: Optional[str] = None
        # The payload of the most recent read, surfaced into the next step's context — each
        # step is stateless, so without this the agent re-reads the same node forever.
        self.last_read: Optional[str] = None

    def _emit(self, event_type: str, **fields) -> None:
        if self.on_event is None:
            return
        try:
            self.on_event({"type": event_type, **fields})
        except Exception:
            logger.exception("on_event callback failed for %s", event_type)

    # ── context (rebuilt fresh each step from durable state) ─────────────────
    def build_context(self, todo: Optional[List[Dict]] = None, stalled: bool = False) -> Dict:
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
            "last_read": self.last_read,
            "stalled": stalled,
            "available_tools": sorted(self.tools),
        }

    # ── completion: decided by validate, never by the agent ──────────────────
    def _failing_components(self) -> set:
        return {f["component_id"] for f in validate(self.spec, self.state)}

    def is_done(self) -> bool:
        return self.spec.frozen and not validate(self.spec, self.state)

    def _target_met(self, target: Dict) -> bool:
        ok, _ = run_check(target["check"], self.state.load_artifact(), self.state.run_dir)
        return ok

    def _view(self, mode: Optional[str]) -> Optional[Dict]:
        projector = self.projectors.get(mode)
        return projector(self.state.load_artifact()) if projector else None

    def run(self) -> ExecutorResult:
        if not self.spec.frozen:
            raise SpecNotFrozenError("build refuses to run until the spec is frozen")

        history: List[StepRecord] = []
        all_ids = {c.get("id") for c in self.spec.components}
        failures = validate(self.spec, self.state)
        passed = all_ids - {f["component_id"] for f in failures}
        self._emit("build_started", n_failing=len(failures), max_steps=self.max_steps,
                   todo=failures)

        step = 0
        while step < self.max_steps:
            if not failures:
                self._emit("build_done", ok=True, steps=step)
                return ExecutorResult(ok=True, steps=step, history=history)

            failing_ids = {f["component_id"] for f in failures}
            mode = next((cid for cid in self.spec.dep_order() if cid in failing_ids), None)
            runner = self.sub_runners.get(mode)
            before = step

            if runner is not None:
                # Stateful sub-loop: drive ONE target check to green, with working memory.
                target = pick_target([f for f in failures if f["component_id"] == mode])
                ctx = self.build_context(todo=failures)
                ctx["target"] = target

                def report(summary: str) -> None:
                    nonlocal step
                    step += 1
                    self.last_result = summary
                    history.append(StepRecord(step=step, action={}, summary=summary))
                    # Live to-do without the expensive compile, so the panel reflects nodes
                    # being written mid-sub-loop instead of freezing on the start snapshot.
                    live = validate(self.spec, self.state, skip_types={"compiles"})
                    print(f"  step {step}/{self.max_steps} [{mode}→{target['check'].get('type')}]: "
                          f"{summary}", flush=True)
                    self._emit("build_step", step=step, max_steps=self.max_steps, mode=mode,
                               summary=summary, target=target["check"].get("type"),
                               n_failing=len(live), todo=live)

                runner(target=target, context=ctx, dispatch=self._dispatch,
                       target_met=lambda: self._target_met(target), report=report,
                       budget=self.max_steps - step, view_fn=lambda: self._view(mode))
                if step == before:        # runner made no move — don't spin forever
                    step += 1
            else:
                step += 1
                t0 = time.perf_counter()
                ctx = self.build_context(todo=failures, stalled=self._is_stalling(history))
                action = self.decide(ctx) or {}
                result = self._dispatch(action)
                self.last_result = self._summarize(action, result)
                self.last_read = self._read_payload(action, result)
                history.append(StepRecord(step=step, action=action, summary=self.last_result))
                dt = time.perf_counter() - t0
                print(f"  step {step}: {self.last_result}  [{dt:.0f}s]", flush=True)

            failures = validate(self.spec, self.state)
            now_failing = {f["component_id"] for f in failures}
            for cid in (all_ids - now_failing) - passed:
                self._fire_milestone(cid)
            passed = all_ids - now_failing

            if runner is None:
                self._emit("build_step", step=step, max_steps=self.max_steps, mode=mode,
                           summary=self.last_result, n_failing=len(failures), todo=failures)

        self._emit("build_done", ok=not failures, steps=step)
        return ExecutorResult(ok=not failures, steps=step, failures=failures, history=history)

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

    @staticmethod
    def _action_sig(action: Dict):
        args = action.get("args") if isinstance(action.get("args"), dict) else {}
        return action.get("tool"), (args.get("node_id") or args.get("component_id"))

    def _is_stalling(self, history: List[StepRecord]) -> bool:
        """Two identical reads in a row = the agent spinning. The next step will be told to
        stop reading and act (read tools are dropped from its choices)."""
        if len(history) < 2:
            return False
        a, b = self._action_sig(history[-1].action), self._action_sig(history[-2].action)
        return a == b and a[0] in _READ_TOOLS

    def _read_payload(self, action: Dict, result: Dict) -> Optional[str]:
        """The content a read returned, for the next step's context. None after a write so
        a stale read doesn't linger."""
        if action.get("tool") not in _READ_TOOLS or not isinstance(result, dict) or result.get("error"):
            return None
        for key in ("content", "story_state"):
            if key in result:
                val = result[key]
                text = val if isinstance(val, str) else json.dumps(val, ensure_ascii=False)
                if len(text) > 2000:
                    text = text[:2000] + " …(truncated)"
                return f"{self._summarize(action, result)}\n{text}"
        return None
