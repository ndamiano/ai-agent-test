import json
import logging
from typing import Optional

from tools.tool_manager import tool_manager
from database.task_store import task_store

logger = logging.getLogger(__name__)


@tool_manager.tool(
    description="List all subtasks for the current task with status, agent, goal, and output preview.",
    auto_inject_context=True,
)
def list_subtasks(task_id: Optional[str] = None) -> str:
    from tools.execution_context import resolve_task_id
    task_id = resolve_task_id(task_id)
    subtasks = task_store.get_subtasks_for_task(task_id)
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
