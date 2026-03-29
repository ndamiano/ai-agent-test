"""
Synthesis tools: list_context_keys, list_subtasks.

Let the summarizer agent discover what was produced during a task.
"""

import json
import logging
from typing import Optional

from tools.tool_manager import tool_manager
from database.task_store import TaskStore

logger = logging.getLogger(__name__)

_task_store = TaskStore()


def _list_context_keys(task_id: Optional[str] = None) -> str:
    """
    List all context keys available for a task.
    """
    from tools.execution_context import resolve_task_id
    task_id = resolve_task_id(task_id)

    all_context = _task_store.get_all_context(task_id)
    keys = list(all_context.keys())
    return json.dumps({"task_id": task_id, "keys": keys, "count": len(keys)})


def _list_subtasks(task_id: Optional[str] = None) -> str:
    """
    List all subtasks with status, agent, goal, and an output preview.
    """
    from tools.execution_context import resolve_task_id
    task_id = resolve_task_id(task_id)

    subtasks = _task_store.get_subtasks_for_task(task_id)
    summaries = []
    for s in subtasks:
        output_preview = None
        if s.get("output"):
            output_preview = s["output"][:200] + ("..." if len(s["output"]) > 200 else "")
        summaries.append({
            "subtask_id": s["id"],
            "agent_id": s["agent_id"],
            "status": s["status"],
            "goal": s["goal"],
            "has_output": s.get("output") is not None,
            "output_preview": output_preview,
        })
    return json.dumps({"task_id": task_id, "subtasks": summaries, "count": len(summaries)})


def register_synthesis_tools() -> None:
    """Register synthesis tools with the global tool manager."""

    tool_manager.register_tool(
        name="list_context_keys",
        description=(
            "List all context keys available for the current task. "
            "Use this to discover what context has been produced before fetching specific values."
        ),
        parameters={
            "type": "object",
            "properties": {},
            "required": [],
        },
        fn=_list_context_keys,
        auto_inject_context=True,
    )

    tool_manager.register_tool(
        name="list_subtasks",
        description=(
            "List all subtasks for the current task with their status, agent, goal, "
            "and an output preview. Use this to see what work was done."
        ),
        parameters={
            "type": "object",
            "properties": {},
            "required": [],
        },
        fn=_list_subtasks,
        auto_inject_context=True,
    )

    logger.info("Synthesis tools registered: list_context_keys, list_subtasks")