"""Chat-facing spec tools — registered with tool_manager so the Maestro persona
can draft and amend specs in conversation. The build itself runs via the executor
(maestro.run) once the human freezes the spec.
"""

from typing import Dict

from tools.tool_manager import tool_manager
from tools.spec_tools import propose_spec, amend_spec
from maestro.run import create_run


@tool_manager.tool(
    description="Draft a build spec from the user's request and return it for their "
                "review. Creates a run and returns {run_id, spec}. Does NOT build — the "
                "human reviews and freezes the spec first.",
    auto_inject_context=False,
)
def propose_game_spec(request: str) -> Dict:
    from tools.execution_context import get_user_id

    user_id = get_user_id()
    if not user_id:
        raise RuntimeError("no authenticated user in context — cannot create a run")
    run_id = create_run(user_id)
    spec = propose_spec(request, run_id)
    return {"run_id": run_id, "spec": spec}


@tool_manager.tool(
    description="Amend an existing spec mid-build. Requires a reason. Un-freezes the "
                "spec so the build pauses until the human re-approves.",
    auto_inject_context=False,
    param_hints={"changes": {"type": "object"}},
)
def amend_game_spec(run_id: str, changes: Dict, reason: str) -> Dict:
    return amend_spec(run_id, changes, reason)
