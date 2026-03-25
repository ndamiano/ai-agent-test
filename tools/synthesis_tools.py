"""
Synthesis tools: list_context_keys, get_context, list_subtasks, get_subtask_output.

Let the synthesizer agent pull what it needs from the task store on demand
instead of receiving everything upfront.
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
    if task_id is None:
        from tools.execution_context import get_task_id
        task_id = get_task_id()
        if task_id is None:
            raise ValueError("task_id must be provided or available in execution context")

    all_context = _task_store.get_all_context(task_id)
    keys = list(all_context.keys())
    return json.dumps({"task_id": task_id, "keys": keys, "count": len(keys)})


def _get_context(task_id: Optional[str] = None, key: str = "") -> str:
    """
    Retrieve a specific context value by key.
    """
    if task_id is None:
        from tools.execution_context import get_task_id
        task_id = get_task_id()
        if task_id is None:
            raise ValueError("task_id must be provided or available in execution context")

    value = _task_store.get_context(task_id, key)
    if value is None:
        return json.dumps({"error": f"Context key '{key}' not found", "task_id": task_id})
    return json.dumps({"task_id": task_id, "key": key, "value": value})


def _list_subtasks(task_id: Optional[str] = None) -> str:
    """
    List all subtasks with status, agent, goal, and an output preview.
    """
    if task_id is None:
        from tools.execution_context import get_task_id
        task_id = get_task_id()
        if task_id is None:
            raise ValueError("task_id must be provided or available in execution context")

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


def _get_subtask_output(subtask_id: str) -> str:
    """
    Get the full output of a specific subtask.
    """
    subtask = _task_store.get_subtask(subtask_id)
    if subtask.get("output") is None:
        return json.dumps({
            "subtask_id": subtask_id,
            "status": subtask["status"],
            "error": "Subtask has no output (may not be completed yet)"
        })
    return json.dumps({
        "subtask_id": subtask_id,
        "agent_id": subtask["agent_id"],
        "status": subtask["status"],
        "goal": subtask["goal"],
        "output": subtask["output"],
    })


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
        name="get_context",
        description=(
            "Retrieve the value of a specific context key for the current task. "
            "Use list_context_keys first to see what keys are available."
        ),
        parameters={
            "type": "object",
            "properties": {
                "key": {
                    "type": "string",
                    "description": "The context key to retrieve.",
                },
            },
            "required": ["key"],
        },
        fn=_get_context,
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

    tool_manager.register_tool(
        name="get_subtask_output",
        description=(
            "Get the full output of a specific subtask by ID. "
            "Use list_subtasks first to see available subtask IDs."
        ),
        parameters={
            "type": "object",
            "properties": {
                "subtask_id": {
                    "type": "string",
                    "description": "The subtask ID to retrieve output for.",
                },
            },
            "required": ["subtask_id"],
        },
        fn=_get_subtask_output,
        auto_inject_context=True,
    )

    logger.info("Synthesis tools registered: list_context_keys, get_context, list_subtasks, get_subtask_output")