"""
TaskRunner: thin entry point that wires the API to MaestroAgent.

Public API is unchanged — the FastAPI router calls create_and_run_background()
and get_status() exactly as before. Internally, PlannerAgent + Orchestrator are
gone; MaestroAgent owns the full lifecycle.
"""

import logging
from typing import Any, Callable, Dict, Optional

from agents.maestro_agent import MaestroAgent
from agents.main_agent import MainAgent
from database.task_store import task_store

logger = logging.getLogger(__name__)


class TaskRunner:
    def __init__(self):
        self.maestro = MaestroAgent()

    # -------------------------------------------------------------------------
    # Create + run
    # -------------------------------------------------------------------------

    def create_and_run(self, goal: str) -> str:
        """Create and run a task synchronously. Returns task_id when done."""
        task_dict = task_store.create_task(goal)
        task_id = task_dict["id"]
        logger.info(f"Task created (sync): {task_id}")
        self.maestro.run(task_id)
        return task_id

    def create_and_run_background(
        self,
        goal: str,
        execution_mode: str = None,  # kept for API compatibility, unused by Maestro
        broadcast_fn: Optional[Callable] = None,
    ) -> str:
        """
        Create a task and run it in the background. Returns task_id immediately.

        broadcast_fn receives WebSocket event dicts as Maestro progresses.
        execution_mode is accepted but ignored — Maestro manages its own
        execution strategy dynamically.
        """
        task_dict = task_store.create_task(goal)
        task_id = task_dict["id"]
        logger.info(f"Task created (background): {task_id}")

        # Fix the lambda capture before spawning the thread
        tid = task_id
        self.maestro.run_background(tid, broadcast_fn=broadcast_fn)

        return task_id

    # -------------------------------------------------------------------------
    # Status + query
    # -------------------------------------------------------------------------

    def get_status(self, task_id: str) -> Dict[str, Any]:
        """Get comprehensive status of a task."""
        try:
            task = task_store.get_task(task_id)
        except KeyError:
            return {"error": f"Task {task_id} not found"}

        subtasks = task_store.get_subtasks_for_task(task_id)
        events = task_store.get_events(task_id)

        total = len(subtasks)
        completed = sum(1 for s in subtasks if s["status"] == "completed")

        if total == 0:
            summary = "Task created, Maestro is planning"
        elif completed == total:
            summary = f"Task completed ({total}/{total} subtasks)"
        else:
            summary = f"In progress ({completed}/{total} subtasks completed)"

        return {
            "task": task,
            "subtasks": subtasks,
            "events": events,
            "summary": summary,
        }

    def ask(self, task_id: str, question: str) -> str:
        """Query a task's accumulated context with a natural language question."""
        try:
            context_chunks = task_store.retrieve_context(task_id, question, k=5)

            if not context_chunks:
                all_context = task_store.get_all_context(task_id)
                if all_context:
                    context_chunks = [
                        {"value": f"{k}: {v}"} for k, v in all_context.items()
                    ]
                else:
                    return "No context found for this task."

            context_text = "\n\n".join(
                f"Context chunk {i + 1}:\n{chunk.get('value', str(chunk))}"
                for i, chunk in enumerate(context_chunks)
            )

            prompt = (
                f"Answer the following question based on the context from task {task_id}.\n\n"
                f"Question: {question}\n\n"
                f"Context:\n{context_text}\n\n"
                "Provide a concise, direct answer based solely on the context provided."
            )

            return MainAgent().chat(prompt)

        except Exception as e:
            logger.error(f"Error querying task {task_id}: {e}")
            return f"Error querying task: {str(e)}"


# Global instance — matches existing import pattern in the router
task_runner = TaskRunner()