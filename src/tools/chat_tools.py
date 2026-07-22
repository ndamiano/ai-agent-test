"""Chat-facing spec tools — registered with tool_manager so the Maestro persona
can draft and amend specs in conversation. The build itself runs via the executor
(maestro.codegen.run) once the human freezes the spec.
"""

from typing import Dict

from maestro.codegen.run import amend_spec, create_run, propose_spec
from tools.execution_context import get_user_id
from tools.safety import log_violation, screen_text
from tools.tool_manager import tool_manager


@tool_manager.tool(
    description="Draft a build spec from the user's request and return it for their "
                "review. Creates a run and returns {run_id, spec}. Does NOT build — the "
                "human reviews and freezes the spec first.",
    auto_inject_context=False,
)
def propose_game_spec(request: str) -> Dict:
    user_id = get_user_id()
    if not user_id:
        raise RuntimeError("no authenticated user in context — cannot create a run")

    violation = screen_text(request)
    if violation is not None:
        log_violation(violation, user_id=user_id, source="spec_request")
        raise RuntimeError(
            "This request can't be built — it matches a category Maestro refuses to generate "
            "(sexual content involving minors)."
        )

    run_id = create_run(user_id)
    spec = propose_spec(request, run_id)
    return {"run_id": run_id, "spec": spec}


@tool_manager.tool(
    description="Amend an existing spec from a free-text note describing the change. "
                "Re-drafts the design and un-freezes the spec so the human re-approves.",
    auto_inject_context=False,
)
def amend_game_spec(run_id: str, note: str) -> Dict:
    return amend_spec(run_id, note)
