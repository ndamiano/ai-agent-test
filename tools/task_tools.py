"""
Task tools: spawn_task, get_task_status.

Orchestration primitives for creating and inspecting subtasks.
Available to MaestroAgent and, selectively, to agents with quality-gate
spawning permissions (spawn_task only, scoped to their own output).

Register at startup via register_task_tools().
"""

import json
import logging
from typing import Optional, Union, List

from tools.tool_manager import tool_manager
from database.validators import validate_dependencies
from database.task_store import TaskStore
from agents.agent_store import AgentStore

logger = logging.getLogger(__name__)

_task_store = TaskStore()
_agent_store = AgentStore()


def _parse_depends_on(depends_on: Union[str, list, None]) -> list:
    """
    Normalise depends_on to a plain Python list.

    LLMs are inconsistent about whether they pass a JSON string or a real list
    for array-typed tool arguments. Accept both so spawn_task never fails on
    a type mismatch.
    """
    if not depends_on:
        return []
    if isinstance(depends_on, list):
        return depends_on
    if isinstance(depends_on, str):
        try:
            parsed = json.loads(depends_on)
        except json.JSONDecodeError as e:
            raise ValueError(f"depends_on is not valid JSON: {e}") from e
        if not isinstance(parsed, list):
            raise ValueError("depends_on must be a JSON array")
        return parsed
    raise ValueError(f"depends_on must be a list or JSON string, got {type(depends_on).__name__}")


def _validate_dependencies(task_id: str, depends_on: List[str]) -> None:
    """
    Validate dependency list for a new subtask.

    Args:
        task_id: The parent task ID
        depends_on: List of dependency subtask IDs to validate

    Raises:
        ValueError: If dependencies are invalid
    """
    validate_dependencies(_task_store, task_id, depends_on)


def _has_circular_dependencies(task_id: str, new_depends_on: List[str]) -> bool:
    """
    Check if adding a new subtask with the given dependencies would create a cycle.

    Args:
        task_id: The parent task ID
        new_depends_on: Dependencies for the new subtask being created

    Returns:
        True if a circular dependency would be created, False otherwise
    """
    from database.validators import has_circular_dependencies
    return has_circular_dependencies(_task_store, task_id, new_depends_on)


def _spawn_task(
    task_id: str,
    agent_id: str,
    goal: str,
    depends_on=None,
    priority: str = "normal",
    name: Optional[str] = None,
    description: Optional[str] = None,
) -> str:
    """
    Create a new subtask under the given task and assign it to an agent.

    Args:
        task_id:    The parent task ID this subtask belongs to.
        agent_id:   The ID of the agent that should execute this subtask.
        goal:       The specific, self-contained instruction for the agent.
        depends_on: List or JSON-string array of subtask IDs that must complete
                    first. e.g. ["abc-123", "def-456"] or '["abc-123"]'.
                    Omit or pass null for no deps.
        priority:   "normal" or "high". Reserved for future scheduling use.

    Returns:
        JSON string with the created subtask's id and status.
    """
    if not _agent_store.exists(agent_id):
        raise ValueError(f"Agent '{agent_id}' not found in agent store.")

    _task_store.get_task(task_id)  # raises KeyError if missing

    dep_ids = _parse_depends_on(depends_on)
    
    # Validate dependencies before creating the subtask
    _validate_dependencies(task_id, dep_ids)

    existing = _task_store.get_subtasks_for_task(task_id)
    position = max((s["position"] for s in existing), default=-1) + 1

    subtask = _task_store.create_subtask(
        task_id=task_id,
        agent_id=agent_id,
        goal=goal,
        position=position,
        depends_on=dep_ids if dep_ids else None,
        name=name,
        description=description,
    )

    logger.info(
        f"spawn_task: created subtask {subtask['id']} "
        f"(agent={agent_id}, position={position}, deps={dep_ids})"
    )

    return json.dumps({
        "subtask_id": subtask["id"],
        "agent_id": agent_id,
        "position": position,
        "status": "pending",
        "depends_on": dep_ids,
    })


def _get_task_status(task_id: Optional[str] = None, subtask_id: Optional[str] = None) -> str:
    """
    Return the current status of a task or a specific subtask.

    If task_id is not provided, uses the current execution context.
    If subtask_id is provided, returns status for that subtask only.
    Otherwise returns the parent task status plus a summary of all subtasks.
    """
    # Use execution context if task_id not provided
    from tools.execution_context import resolve_task_id
    task_id = resolve_task_id(task_id)

    if subtask_id:
        subtask = _task_store.get_subtask(subtask_id)
        return json.dumps({
            "subtask_id": subtask_id,
            "agent_id": subtask["agent_id"],
            "status": subtask["status"],
            "goal": subtask["goal"],
            "has_output": subtask.get("output") is not None,
        })

    task = _task_store.get_task(task_id)
    subtasks = _task_store.get_subtasks_for_task(task_id)

    counts = {"pending": 0, "in_progress": 0, "completed": 0, "failed": 0}
    summaries = []
    for s in subtasks:
        status = s["status"]
        counts[status] = counts.get(status, 0) + 1
        summaries.append({
            "subtask_id": s["id"],
            "agent_id": s["agent_id"],
            "status": status,
            "position": s["position"],
            "has_output": s.get("output") is not None,
        })

    return json.dumps({
        "task_id": task_id,
        "task_status": task["status"],
        "subtask_counts": counts,
        "subtasks": summaries,
    })


def register_task_tools() -> None:
    """Register task tools with the global tool manager."""

    tool_manager.register_tool(
        name="spawn_task",
        description=(
            "Create a new subtask assigned to a specific agent. "
            "Use this to build or extend the execution plan. "
            "Tasks whose dependencies are all completed will run automatically. "
            "Returns the new subtask ID which can be used in future depends_on lists."
        ),
        parameters={
            "type": "object",
            "properties": {
                "agent_id": {
                    "type": "string",
                    "description": "ID of the agent to assign this subtask to.",
                },
                "goal": {
                    "type": "string",
                    "description": (
                        "The specific, self-contained instruction for the agent. "
                        "Write this as if the agent has no prior context — "
                        "include everything it needs."
                    ),
                },
                "name": {
                    "type": "string",
                    "description": (
                        "A short, human-readable name for this subtask "
                        "(e.g. 'Write unit tests', 'Deploy to staging'). "
                        "Displayed in the UI instead of the raw agent ID."
                    ),
                },
                "description": {
                    "type": "string",
                    "description": (
                        "A brief description of what this subtask does. "
                        "Shown beneath the name in the UI for additional context."
                    ),
                },
                "depends_on": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Array of subtask IDs that must complete before this one runs. "
                        "Example: [\"abc-123\", \"def-456\"]. Omit for no dependencies."
                    ),
                },
                "priority": {
                    "type": "string",
                    "description": '"normal" or "high". Defaults to "normal".',
                },
            },
            "required": ["agent_id", "goal"],
        },
        fn=_spawn_task,
        auto_inject_context=True,
    )

    tool_manager.register_tool(
        name="get_task_status",
        description=(
            "Get the current status of a task and all its subtasks, or a specific subtask. "
            "Use this to check whether a wave of work is complete before evaluating outputs. "
            "The task_id will be automatically inferred from the current execution context if not provided."
        ),
        parameters={
            "type": "object",
            "properties": {
                "task_id": {
                    "type": "string",
                    "description": "Optional. The parent task ID to check. Defaults to current task context.",
                },
                "subtask_id": {
                    "type": "string",
                    "description": "Optional. A specific subtask ID for a targeted status check.",
                },
            },
            "required": [],
        },
        fn=_get_task_status,
        auto_inject_context=True,
    )

    logger.info("Task tools registered: spawn_task, get_task_status")