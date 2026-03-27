"""
Shared validators for dependency checking.

Extracted from tools.task_tools to resolve the layer violation where
the database layer (TaskStore) imported from the tools layer.
"""

from typing import List, Optional


def validate_dependencies(task_store, task_id: str, depends_on: Optional[List[str]]) -> None:
    """
    Validate dependency list for a new subtask.

    Args:
        task_store: TaskStore instance to fetch existing subtasks
        task_id: The parent task ID
        depends_on: List of dependency subtask IDs to validate

    Raises:
        ValueError: If dependencies are invalid
    """
    if not depends_on:
        return

    existing_subtasks = task_store.get_subtasks_for_task(task_id)
    existing_ids = {s["id"] for s in existing_subtasks}

    missing_deps = [dep_id for dep_id in depends_on if dep_id not in existing_ids]
    if missing_deps:
        raise ValueError(
            f"Invalid dependencies: {missing_deps}. "
            f"These subtask IDs do not exist in task {task_id}. "
            f"Existing subtasks: {list(existing_ids)}"
        )

    if has_circular_dependencies(task_store, task_id, depends_on):
        raise ValueError(
            f"Circular dependency detected in task {task_id}. "
            f"Dependencies {depends_on} would create a cycle."
        )


def has_circular_dependencies(task_store, task_id: str, new_depends_on: List[str]) -> bool:
    """
    Check if adding a new subtask with the given dependencies would create a cycle.

    Uses depth-first search to detect cycles in the dependency graph.

    Args:
        task_store: TaskStore instance to fetch existing subtasks
        task_id: The parent task ID
        new_depends_on: Dependencies for the new subtask being created

    Returns:
        True if a circular dependency would be created, False otherwise
    """
    subtasks = task_store.get_subtasks_for_task(task_id)
    graph = {}

    for subtask in subtasks:
        subtask_id = subtask["id"]
        deps = subtask.get("depends_on") or []
        graph[subtask_id] = deps

    placeholder_id = "NEW_SUBTASK"
    graph[placeholder_id] = new_depends_on

    visited = set()
    rec_stack = set()

    def has_cycle_dfs(node: str) -> bool:
        visited.add(node)
        rec_stack.add(node)

        neighbors = graph.get(node, [])

        for neighbor in neighbors:
            if neighbor not in graph:
                continue
            if neighbor not in visited:
                if has_cycle_dfs(neighbor):
                    return True
            elif neighbor in rec_stack:
                return True

        rec_stack.remove(node)
        return False

    return has_cycle_dfs(placeholder_id)
