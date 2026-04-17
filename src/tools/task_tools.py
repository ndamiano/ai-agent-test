import json
import logging
from typing import Optional, Union, List

from tools.tool_manager import tool_manager
from database.validators import validate_dependencies
from database.task_store import task_store
logger = logging.getLogger(__name__)


def _parse_depends_on(depends_on: Union[str, list, None]) -> list:
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


def _expand_dep_ids(task_id: str, dep_ids: list) -> list:
    """Expand short ID prefixes to full UUIDs. Raises if a prefix is ambiguous or unmatched."""
    if not dep_ids:
        return dep_ids
    existing = {s["id"] for s in task_store.get_subtasks_for_task(task_id)}
    expanded = []
    for dep in dep_ids:
        if dep in existing:
            expanded.append(dep)
            continue
        matches = [full_id for full_id in existing if full_id.startswith(dep)]
        if len(matches) == 1:
            expanded.append(matches[0])
        elif len(matches) > 1:
            raise ValueError(f"Ambiguous dependency prefix '{dep}' matches multiple subtasks: {matches}")
        else:
            expanded.append(dep)  # leave as-is; validate_dependencies will report the error
    return expanded


def _spawn_task(
    task_id: str,
    agent_id: str,
    goal: str,
    depends_on=None,
    priority: str = "normal",
    name: Optional[str] = None,
    description: Optional[str] = None,
) -> str:
    task_store.get_task(task_id)  # raises KeyError if missing

    dep_ids = _parse_depends_on(depends_on)
    dep_ids = _expand_dep_ids(task_id, dep_ids)
    validate_dependencies(task_store, task_id, dep_ids)

    existing = task_store.get_subtasks_for_task(task_id)
    position = max((s["position"] for s in existing), default=-1) + 1

    subtask = task_store.create_subtask(
        task_id=task_id,
        agent_id=agent_id,
        goal=goal,
        position=position,
        depends_on=dep_ids if dep_ids else None,
        name=name,
        description=description,
    )

    logger.info(f"spawn_task: created subtask {subtask['id']} (agent={agent_id}, position={position}, deps={dep_ids})")

    return json.dumps({
        "subtask_id": subtask["id"],
        "agent_id": agent_id,
        "position": position,
        "status": "pending",
        "depends_on": dep_ids,
    })


def _spawn_domain(
    task_id: str,
    name: str,
    goal: str,
    description: Optional[str] = None,
    depends_on=None,
) -> str:
    """
    Spawn a sub-orchestrator (child maestro) that will independently plan and execute
    a domain of work. The child maestro gets its own FSM cycle (plan → execute → validate → compile).
    Returns subtask_id and child_task_id.
    """
    parent_task = task_store.get_task(task_id)

    dep_ids = _parse_depends_on(depends_on)
    dep_ids = _expand_dep_ids(task_id, dep_ids)
    validate_dependencies(task_store, task_id, dep_ids)

    # Create the child task linked to the parent
    child_task = task_store.create_task(
        goal=goal,
        execution_mode=parent_task.get("execution_mode", "sequential"),
        working_directory=parent_task.get("working_directory"),
        parent_task_id=task_id,
    )
    child_task_id = child_task["id"]

    # Create placeholder subtask in parent with maestro as agent
    existing = task_store.get_subtasks_for_task(task_id)
    position = max((s["position"] for s in existing), default=-1) + 1

    subtask = task_store.create_subtask(
        task_id=task_id,
        agent_id="maestro",
        goal=goal,
        position=position,
        depends_on=dep_ids if dep_ids else None,
        name=name,
        description=description,
        input_context={"child_task_id": child_task_id},
    )

    logger.info(f"spawn_domain: created domain subtask {subtask['id']} → child task {child_task_id} (name={name})")

    return json.dumps({
        "subtask_id": subtask["id"],
        "child_task_id": child_task_id,
        "name": name,
        "status": "pending",
        "depends_on": dep_ids,
    })


def register_task_tools() -> None:
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
                    "description": "A short, human-readable name for this subtask (e.g. 'Write unit tests').",
                },
                "description": {
                    "type": "string",
                    "description": "A brief description of what this subtask does.",
                },
                "depends_on": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": 'Array of subtask IDs that must complete before this one runs. Example: ["abc-123"].',
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
        name="spawn_domain",
        description=(
            "Spawn a child maestro to independently plan and execute a self-contained domain of work. "
            "Use this when a major area of the task (e.g. combat system, story, enemy roster, NPC dialogue) "
            "is complex enough to need its own planning, multi-step execution, and validation cycle — "
            "rather than a single worker subtask. "
            "The child maestro will break its domain goal into subtasks, run them, and validate results. "
            "Returns the subtask_id (for depends_on) and the child_task_id."
        ),
        parameters={
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Short human-readable name for this domain (e.g. 'Combat System', 'Enemy Roster').",
                },
                "goal": {
                    "type": "string",
                    "description": (
                        "Full, self-contained description of what this domain must produce. "
                        "Include: what to build, key design decisions, tech constraints, output format, "
                        "and any specific requirements from the parent task's acceptance criteria that "
                        "this domain is responsible for satisfying."
                    ),
                },
                "description": {
                    "type": "string",
                    "description": "One-line summary of this domain shown in the UI.",
                },
                "depends_on": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Subtask IDs (from this task) that must complete before this domain starts.",
                },
            },
            "required": ["name", "goal"],
        },
        fn=_spawn_domain,
        auto_inject_context=True,
    )

    logger.info("Task tools registered: spawn_task, spawn_domain")
