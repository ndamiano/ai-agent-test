"""The per-step build context — a fresh, minimal snapshot rebuilt from durable on-disk state each
gate sweep (never the transcript), so context stays ~constant as the artifact grows.

`Context` is what `get_errors` reads: the assembled artifact + the frozen spec.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class Context:
    spec: Dict                       # the frozen spec
    state: object                    # RunState
    artifact: Dict                   # the assembled artifact, loaded once this step
    errors: List = field(default_factory=list)   # the effective to-do this step (Error objects)
    last_result: Optional[str] = None
    last_read: Optional[str] = None
    stalled: bool = False

    def story_state(self) -> Dict:
        return self.state.read_story_state() or {}


def build_context(spec: Dict, state, errors: Optional[List] = None, last_result: Optional[str] = None,
                  last_read: Optional[str] = None, stalled: bool = False) -> Context:
    return Context(spec=spec, state=state, artifact=state.load_artifact(), errors=errors or [],
                   last_result=last_result, last_read=last_read, stalled=stalled)
