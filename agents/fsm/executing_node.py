"""
Executing state node: Execute ready subtasks in parallel.
"""

import logging
import asyncio
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict
from agents.fsm.state_node import StateNode, StateContext
from config.time_utils import get_utc_timestamp

logger = logging.getLogger(__name__)


class ExecutingNode(StateNode):
    """
    EXECUTING state: Execute all ready subtasks in parallel.

    Entry: Subtasks exist and some are ready to execute
    Exit: All subtasks in current wave have completed or failed
    Transition: Always → ValidatingNode
    """

    @property
    def state_name(self) -> str:
        return "EXECUTING"

    async def execute_async(self, context: StateContext) -> StateNode:
        """Execute ready subtasks in parallel."""
        from agents.fsm.validating_node import ValidatingNode

        # Increment wave counter (enforces max_waves safety valve)
        context.increment_wave()

        # Broadcast state entry
        self._broadcast(context, {
            "type": "fsm_state_change",
            "task_id": context.task_id,
            "from_state": "PLANNING",
            "to_state": self.state_name,
            "wave": context.wave_count,
            "timestamp": get_utc_timestamp(),
        })

        logger.info(f"Maestro: task {context.task_id} — wave {context.wave_count} — EXECUTING")

        # Execute all ready subtasks
        await self._execute_wave(context)

        # Always transition to VALIDATING after execution
        return ValidatingNode()

    async def _execute_wave(self, context: StateContext) -> None:
        """
        Execute all currently ready subtasks in parallel.
        Blocks until every submitted subtask either completes or fails.
        """
        ready = context.task_store.get_ready_subtasks(context.task_id)
        if not ready:
            logger.info(f"No ready subtasks for task {context.task_id} in wave {context.wave_count}")
            return

        # Get or create executor (reuse if available)
        import os
        max_workers = int(os.getenv("MAX_PARALLEL_WORKERS", "2"))
        executor = ThreadPoolExecutor(max_workers=max_workers)

        try:
            # Submit all subtasks
            futures = {
                executor.submit(self._execute_subtask, s, context): s
                for s in ready
            }

            # Wait for all to complete using run_in_executor for async compatibility
            loop = asyncio.get_event_loop()
            for future in as_completed(futures):
                subtask = futures[future]
                try:
                    # Await the future result in async context
                    await loop.run_in_executor(None, future.result)
                except Exception as e:
                    logger.error(f"Maestro: subtask {subtask['id']} failed: {e}")
                    # Status already set to failed inside _execute_subtask

        finally:
            executor.shutdown(wait=True)

    def _execute_subtask(self, subtask: Dict, context: StateContext) -> str:
        """Execute a single subtask using the specified agent."""
        subtask_id = subtask["id"]
        task_id = subtask["task_id"]

        try:
            context.task_store.update_subtask_status(subtask_id, "in_progress")
            self._broadcast(context, {
                "type": "subtask_started",
                "task_id": task_id,
                "subtask_id": subtask_id,
                "agent_id": subtask["agent_id"],
                "timestamp": get_utc_timestamp(),
            })

            # Fetch task to get working_directory
            task = context.task_store.get_task(task_id)
            if task is None:
                raise RuntimeError(f"Task {task_id} not found in task_store")
            working_directory = task.get("working_directory")

            # Refresh subtask to get latest state
            fresh = context.task_store.get_subtask(subtask_id)
            if fresh is None:
                raise RuntimeError(f"Subtask {subtask_id} not found in task_store")

            # Build context for this subtask
            context_text = self._build_context_for_subtask(task_id, fresh, context)

            # Default to worker if the specified agent doesn't exist
            agent_id = fresh["agent_id"]
            DEFAULT_AGENT_ID = "worker"
            if not context.agent_store.exists(agent_id):
                logger.warning(f"Agent '{agent_id}' not found, using default '{DEFAULT_AGENT_ID}'")
                agent_id = DEFAULT_AGENT_ID

            # Instantiate agent
            from agents.main_agent import MainAgent
            agent = MainAgent(agent_id=agent_id)
            agent.set_broadcast_context(task_id, subtask_id, context.broadcast_fn)

            # Build message
            message = f"{context_text}\n\nTask: {fresh['goal']}"

            # Execute within execution context
            from tools.execution_context import execution_context
            with execution_context(task_id=task_id, subtask_id=subtask_id, working_directory=working_directory):
                output = agent.chat(message)

            # Store output and mark completed
            context.task_store.set_subtask_output(subtask_id, output)
            self._broadcast(context, {
                "type": "subtask_completed",
                "task_id": task_id,
                "subtask_id": subtask_id,
                "agent_id": agent_id,
                "timestamp": get_utc_timestamp(),
            })

            return output

        except Exception as e:
            # Log detailed error information
            import traceback
            error_details = f"Subtask {subtask_id} failed: {e}\nTraceback:\n{traceback.format_exc()}"
            logger.error(error_details)

            context.task_store.update_subtask_status(subtask_id, "failed")
            context.task_store.log_event(
                task_id, "subtask_failed", error_details, subtask_id
            )
            self._broadcast(context, {
                "type": "subtask_failed",
                "task_id": task_id,
                "subtask_id": subtask_id,
                "error": str(e),
                "timestamp": get_utc_timestamp(),
            })
            raise

    def _build_context_for_subtask(self, task_id: str, subtask: Dict, context: StateContext) -> str:
        """Build context string for a subtask from task goal, context keys, and dependencies."""
        if subtask is None:
            raise RuntimeError(f"Subtask is None when building context for task {task_id}")

        task = context.task_store.get_task(task_id)
        if task is None:
            raise RuntimeError(f"Task {task_id} not found when building context for subtask {subtask.get('id', 'unknown')}")

        lines = [f"Overall task goal: {task['goal']}", ""]

        # Context keys
        input_context = subtask.get("input_context")
        if input_context is None:
            context_keys = []
        elif isinstance(input_context, dict):
            context_keys = input_context.get("context_keys", [])
        else:
            logger.warning(f"Unexpected input_context type for subtask {subtask.get('id')}: {type(input_context)}")
            context_keys = []
        if context_keys:
            for key in context_keys:
                value = context.task_store.get_context(task_id, key)
                if value:
                    lines.append(f"Context key '{key}': {value}")
            lines.append("")

        # Dependency outputs
        depends_on = subtask.get("depends_on", [])
        if depends_on:
            lines.append("Outputs from dependencies:")
            for dep_id in depends_on:
                try:
                    dep = context.task_store.get_subtask(dep_id)
                    if dep and dep.get("output"):
                        agent_id = dep.get("agent_id", "unknown")
                        lines.append(f"\nOutput from {agent_id}:")
                        lines.append(dep["output"])
                except Exception as e:
                    logger.warning(f"Could not fetch dependency {dep_id}: {e}")
            lines.append("")

        return "\n".join(lines)
