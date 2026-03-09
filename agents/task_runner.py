from typing import Dict, Any, Optional, Callable
import threading
import logging
from agents.planner import PlannerAgent
from agents.orchestrator import Orchestrator
from database.task_store import task_store
from agents.context_builder import ContextBuilder
from agents.main_agent import MainAgent
from agents.agent_store import AgentStore

logger = logging.getLogger(__name__)

class TaskRunner:
    def __init__(self):
        self.planner = PlannerAgent()
        self.orchestrator = Orchestrator()
        self.context_builder = ContextBuilder(task_store)
        self.main_agent = MainAgent()

    def create_and_run(self, goal: str, execution_mode: str = None) -> str:
        """Create and run a task synchronously."""
        if execution_mode is None:
            execution_mode = 'sequential'  # Default to sequential

        # Create task
        task_dict = task_store.create_task(goal, execution_mode)
        task_id = task_dict["id"]
        logger.info(f"Task created: {task_id}")

        # Plan the task
        self.planner.run(goal, task_id)
        logger.info(f"Task planned: {task_id}")

        # Execute the task
        self.orchestrator.run_task(task_id)
        logger.info(f"Task completed: {task_id}")

        return task_id

    def create_and_run_background(self, goal: str, execution_mode: str = None, broadcast_fn: Optional[Callable] = None) -> str:
        """Create and run a task asynchronously in the background."""
        if execution_mode is None:
            execution_mode = 'sequential'  # Default to sequential

        # Default to sequential if execution_mode is not sequential or parallel
        if execution_mode not in ['sequential', 'parallel']:
            execution_mode = 'sequential'

        # Create task
        task_dict = task_store.create_task(goal, execution_mode)
        task_id = task_dict["id"]
        logger.info(f"Background task created: {task_id}")

        # Execute everything in background thread
        def run_task():
            try:
                # Plan the task
                self.planner.run(goal, task_id)
                logger.info(f"Background task planned: {task_id}")

                # Execute the task
                self.orchestrator.run_task(task_id, broadcast_fn=broadcast_fn)
                logger.info(f"Background task completed: {task_id}")
            except Exception as e:
                logger.error(f"Background task failed: {task_id}, error: {e}")

        thread = threading.Thread(target=run_task, daemon=True)
        thread.start()

        return task_id

    def get_status(self, task_id: str) -> Dict[str, Any]:
        """Get comprehensive status of a task."""
        try:
            task = task_store.get_task(task_id)
        except KeyError:
            return {"error": f"Task {task_id} not found"}

        subtasks = task_store.get_subtasks_for_task(task_id)
        events = task_store.get_events(task_id)

        # Build summary
        total_subtasks = len(subtasks)
        completed_subtasks = len([s for s in subtasks if s['status'] == 'completed'])

        if total_subtasks == 0:
            progress_summary = "Task created, planning in progress"
        elif completed_subtasks == total_subtasks:
            progress_summary = f"Task completed successfully ({total_subtasks}/{total_subtasks} subtasks)"
        else:
            progress_summary = f"Task in progress ({completed_subtasks}/{total_subtasks} subtasks completed)"

        return {
            "task": task,
            "subtasks": subtasks,
            "events": events,
            "summary": progress_summary
        }

    def ask(self, task_id: str, question: str) -> str:
        """Query a task's accumulated context with a natural language question."""
        try:
            # Get relevant context chunks
            context_chunks = task_store.retrieve_context(task_id, question, k=5)

            if not context_chunks:
                # Try to get all context as fallback
                all_context = task_store.get_all_context(task_id)
                if all_context:
                    context_text = "\n\n".join([f"{key}: {value}" for key, value in all_context.items()])
                    context_chunks = [{"value": context_text}]
                else:
                    return "No context found for this task. The task may not exist or have no recorded context yet."

            # Format context for the main agent
            context_text = "\n\n".join([
                f"Context chunk {i+1}:\n{chunk.get('value', chunk.get('text', str(chunk)))}"
                for i, chunk in enumerate(context_chunks)
            ])

            # Create a prompt for the main agent
            prompt = f"""Answer the following question based on the provided context from task {task_id}:

Question: {question}

Context:
{context_text}

Please provide a concise, direct answer based solely on the context provided."""

            # Create a fresh MainAgent instance to avoid message history sharing
            fresh_agent = MainAgent()
            response = fresh_agent.chat(prompt)
            return response

        except Exception as e:
            logger.error(f"Error querying task {task_id}: {e}")
            return f"Error querying task: {str(e)}"

# Global instance
task_runner = TaskRunner()
