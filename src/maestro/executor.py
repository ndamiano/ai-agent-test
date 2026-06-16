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


class SpecNotFrozenError(RuntimeError):
    pass


class Executor:
    """
    tools:  name -> callable(**args) -> result (dict). The artifact/spec tools.
    decide: callable(context) -> {"tool": str, "args": dict}. The agent's choice.
    """

    def __init__(self, spec, state, tools: Dict[str, Callable], decide: Callable,
                 max_steps: int = 60):
        self.spec = spec
        self.state = state
        self.tools = tools
        self.decide = decide
        self.max_steps = max_steps
        self.last_result: Optional[str] = None

    # ── context (rebuilt fresh each step from durable state) ─────────────────
    def build_context(self) -> Dict:
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
            "todo": validate(self.spec, self.state),
            "scratchpad": self.state.read_scratchpad(),
            "last_result": self.last_result,
            "available_tools": sorted(self.tools),
        }

    # ── completion: decided by validate, never by the agent ──────────────────
    def is_done(self) -> bool:
        return self.spec.frozen and not validate(self.spec, self.state)

    def run(self) -> ExecutorResult:
        if not self.spec.frozen:
            raise SpecNotFrozenError("build refuses to run until the spec is frozen")

        history: List[StepRecord] = []
        for step in range(1, self.max_steps + 1):
            if self.is_done():
                return ExecutorResult(ok=True, steps=step - 1, history=history)

            action = self.decide(self.build_context()) or {}
            result = self._dispatch(action)
            self.last_result = self._summarize(action, result)
            history.append(StepRecord(step=step, action=action, summary=self.last_result))

        return ExecutorResult(
            ok=self.is_done(),
            steps=self.max_steps,
            failures=validate(self.spec, self.state),
            history=history,
        )

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
        if isinstance(result, dict) and result.get("error"):
            return f"{name}: error — {result['error']}"
        return f"{name}: ok"
