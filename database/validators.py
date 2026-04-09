from typing import List, Optional


def validate_dependencies(task_store, task_id: str, depends_on: Optional[List[str]]) -> None:
    if not depends_on:
        return
    existing_ids = {s["id"] for s in task_store.get_subtasks_for_task(task_id)}
    missing = [dep for dep in depends_on if dep not in existing_ids]
    if missing:
        raise ValueError(
            f"Invalid dependencies: {missing}. "
            f"These subtask IDs do not exist in task {task_id}. "
            f"Existing subtasks: {list(existing_ids)}"
        )
    if has_circular_dependencies(task_store, task_id, depends_on):
        raise ValueError(f"Circular dependency detected in task {task_id}. Dependencies {depends_on} would create a cycle.")


def has_circular_dependencies(task_store, task_id: str, new_depends_on: List[str]) -> bool:
    subtasks = task_store.get_subtasks_for_task(task_id)
    graph = {s["id"]: s.get("depends_on") or [] for s in subtasks}
    graph["NEW_SUBTASK"] = new_depends_on

    visited, rec_stack = set(), set()

    def has_cycle(node: str) -> bool:
        visited.add(node)
        rec_stack.add(node)
        for neighbor in graph.get(node, []):
            if neighbor not in graph:
                continue
            if neighbor not in visited and has_cycle(neighbor):
                return True
            if neighbor in rec_stack:
                return True
        rec_stack.remove(node)
        return False

    return has_cycle("NEW_SUBTASK")
