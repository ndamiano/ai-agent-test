"""
Orchestration tools: set_acceptance_criteria, update_checklist, read_plan.

Planning primitives for Maestro to define and track the definition of done.
Available to Maestro only.

Register at startup via register_orchestration_tools().
"""

import json
import logging

from tools.tool_manager import tool_manager
from database.task_store import task_store

logger = logging.getLogger(__name__)

_CRITERIA_KEY = "acceptance_criteria"
_CHECKLIST_KEY = "maestro_checklist"


def _set_acceptance_criteria(task_id: str, criteria: list) -> str:
    """Set the acceptance criteria for this task. Can only be called once."""
    existing = task_store.get_context(task_id, _CRITERIA_KEY)
    if existing is not None:
        return json.dumps({"error": "Acceptance criteria already set. Use read_plan to review them."})

    task_store.write_context(task_id, _CRITERIA_KEY, json.dumps(criteria))
    logger.info(f"set_acceptance_criteria: wrote {len(criteria)} criteria for task {task_id}")
    return json.dumps({"ok": True, "criteria_count": len(criteria)})


def _update_checklist(task_id: str, items: list) -> str:
    """Replace the maestro planning checklist."""
    task_store.write_context(task_id, _CHECKLIST_KEY, json.dumps(items))
    logger.info(f"update_checklist: wrote {len(items)} items for task {task_id}")
    return json.dumps({"ok": True, "item_count": len(items)})


def _read_plan(task_id: str) -> str:
    """Return the current acceptance criteria and checklist."""
    criteria_raw = task_store.get_context(task_id, _CRITERIA_KEY)
    checklist_raw = task_store.get_context(task_id, _CHECKLIST_KEY)

    return json.dumps({
        "acceptance_criteria": json.loads(criteria_raw) if criteria_raw else None,
        "checklist": json.loads(checklist_raw) if checklist_raw else None,
    })


def register_orchestration_tools() -> None:
    """Register orchestration tools with the global tool manager."""

    tool_manager.register_tool(
        name="set_acceptance_criteria",
        description=(
            "Define the acceptance criteria for this task. "
            "Call this ONCE at the very start of planning, before spawning any subtasks. "
            "These are the concrete, verifiable things that must all be true for the goal to be complete. "
            "Returns an error if criteria have already been set."
        ),
        parameters={
            "type": "object",
            "properties": {
                "criteria": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {
                                "type": "string",
                                "description": "Short unique ID, e.g. 'AC-1'.",
                            },
                            "criterion": {
                                "type": "string",
                                "description": "What must be true for this criterion to pass.",
                            },
                            "rationale": {
                                "type": "string",
                                "description": "Why this criterion matters for the goal.",
                            },
                        },
                        "required": ["id", "criterion", "rationale"],
                    },
                    "description": "List of concrete, verifiable acceptance criteria.",
                },
            },
            "required": ["criteria"],
        },
        fn=_set_acceptance_criteria,
        auto_inject_context=True,
    )

    tool_manager.register_tool(
        name="update_checklist",
        description=(
            "Replace the planning checklist with a current snapshot of all work items. "
            "Call this each planning pass to reflect what has been done and what remains. "
            "Each item should link to the acceptance criterion it helps satisfy."
        ),
        parameters={
            "type": "object",
            "properties": {
                "items": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {
                                "type": "string",
                                "description": "Short unique ID, e.g. 'T-1'.",
                            },
                            "item": {
                                "type": "string",
                                "description": "What this work item does.",
                            },
                            "status": {
                                "type": "string",
                                "enum": ["pending", "in_progress", "done", "failed"],
                                "description": "Current status of this item.",
                            },
                            "linked_criteria_id": {
                                "type": "string",
                                "description": "ID of the acceptance criterion this item helps satisfy.",
                            },
                            "notes": {
                                "type": "string",
                                "description": "Optional notes on this item.",
                            },
                        },
                        "required": ["id", "item", "status", "linked_criteria_id"],
                    },
                    "description": "Full list of planning checklist items.",
                },
            },
            "required": ["items"],
        },
        fn=_update_checklist,
        auto_inject_context=True,
    )

    tool_manager.register_tool(
        name="read_plan",
        description=(
            "Read the current acceptance criteria and planning checklist. "
            "Call this at the start of each re-planning pass to orient yourself "
            "before deciding what work remains."
        ),
        parameters={
            "type": "object",
            "properties": {},
            "required": [],
        },
        fn=_read_plan,
        auto_inject_context=True,
    )

    logger.info("Orchestration tools registered: set_acceptance_criteria, update_checklist, read_plan")
