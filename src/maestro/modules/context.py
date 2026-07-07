"""The per-step build context — a fresh, minimal snapshot rebuilt from durable on-disk state each
loop step (never the transcript), so context stays ~constant as the artifact grows.

`Context` is what `get_errors` reads (the assembled artifact + the spec's resolved `params`).
`render_dict` turns it into the dict the leaf renderers in `maestro.context_render` and a module's
own `render_context` consume when building a `get_correction_prompt`.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class Context:
    spec: Dict                       # spec data: title/request/modules/params/story_state_schema/engine
    state: object                    # RunState
    artifact: Dict                   # the assembled artifact, loaded once this step
    errors: List = field(default_factory=list)   # the effective to-do this step (Error objects)
    last_result: Optional[str] = None
    last_read: Optional[str] = None
    stalled: bool = False

    @property
    def run_dir(self):
        return self.state.run_dir

    @property
    def engine(self) -> str:
        return self.spec.get("engine", "renpy")

    def param(self, name: str, default: int = 0) -> int:
        """A resolved sizing knob from the frozen spec (>= the module's floor)."""
        return (self.spec.get("params") or {}).get(name, default)

    def component(self, cid: str):
        return self.artifact.get(cid)

    def story_state(self) -> Dict:
        return self.state.read_story_state() or {}


def build_context(spec: Dict, state, errors: Optional[List] = None, last_result: Optional[str] = None,
                  last_read: Optional[str] = None, stalled: bool = False) -> Context:
    return Context(spec=spec, state=state, artifact=state.load_artifact(), errors=errors or [],
                   last_result=last_result, last_read=last_read, stalled=stalled)


def render_dict(ctx: Context, *, active: Optional[str], target=None, active_view: Optional[Dict] = None,
                available_tools: Optional[List[str]] = None, slot_index: int = 0) -> Dict:
    """The dict the renderers read. There is NO generic upstream dump: each module's
    `render_context` composes its own component blocks from `ctx.artifact` (the consumer knows
    what its call needs; a producer-side trim starved the dialogue author of the character card)."""
    return {
        "spec": {k: v for k, v in ctx.spec.items() if k != "frozen"},
        "mode": active,
        "todo": ctx.errors,
        "target": target,
        "active_view": active_view,
        "story_state": ctx.story_state(),
        "last_result": ctx.last_result,
        "last_read": ctx.last_read,
        "stalled": ctx.stalled,
        "available_tools": sorted(available_tools or []),
        "slot_index": slot_index,
    }
