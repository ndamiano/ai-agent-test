import logging
import asyncio
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict
from agents.fsm.state_node import StateNode, StateContext
from config.time_utils import get_utc_timestamp
from api.websocket.event_bus import event_bus

logger = logging.getLogger(__name__)


class ExecutingNode(StateNode):
    @property
    def state_name(self) -> str:
        return "EXECUTING"

    async def execute_async(self, context: StateContext) -> StateNode:
        from agents.fsm.validating_node import ValidatingNode
        context.increment_wave()
        logger.info(f"Maestro: task {context.task_id} — wave {context.wave_count} — EXECUTING")
        await self._execute_wave(context)
        return ValidatingNode()

    async def _execute_wave(self, context: StateContext) -> None:
        ready = context.task_store.get_ready_subtasks(context.task_id)
        if not ready:
            logger.info(f"No ready subtasks for task {context.task_id} in wave {context.wave_count}")
            return

        import os
        executor = ThreadPoolExecutor(max_workers=int(os.getenv("MAX_PARALLEL_WORKERS", "2")))
        try:
            futures = {executor.submit(self._execute_subtask, s, context): s for s in ready}
            loop = asyncio.get_event_loop()
            for future in as_completed(futures):
                try:
                    await loop.run_in_executor(None, future.result)
                except Exception as e:
                    logger.error(f"Maestro: subtask {futures[future]['id']} failed: {e}")
        finally:
            executor.shutdown(wait=True)

    def _execute_subtask(self, subtask: Dict, context: StateContext) -> str:
        subtask_id = subtask["id"]
        task_id = subtask["task_id"]

        try:
            context.task_store.update_subtask_status(subtask_id, "in_progress")
            event_bus.publish_sync({
                "type": "subtask_started",
                "task_id": task_id,
                "subtask_id": subtask_id,
                "agent_id": subtask["agent_id"],
                "timestamp": get_utc_timestamp(),
            })

            task = context.task_store.get_task(task_id)
            working_directory = task.get("working_directory")

            fresh = context.task_store.get_subtask(subtask_id)
            context_text = self._build_context_for_subtask(task_id, fresh, context)

            agent_id = fresh["agent_id"]
            if not context.agent_store.exists(agent_id):
                logger.warning(f"Agent '{agent_id}' not found, using 'worker'")
                agent_id = "worker"

            from agents.main_agent import MainAgent
            agent = MainAgent(agent_id=agent_id)

            from tools.execution_context import execution_context
            with execution_context(task_id=task_id, subtask_id=subtask_id, working_directory=working_directory):
                output = agent.chat(f"{context_text}\n\nTask: {fresh['goal']}")

            context.task_store.set_subtask_output(subtask_id, output)
            event_bus.publish_sync({
                "type": "subtask_completed",
                "task_id": task_id,
                "subtask_id": subtask_id,
                "agent_id": agent_id,
                "timestamp": get_utc_timestamp(),
            })
            return output

        except Exception as e:
            import traceback
            error_details = f"Subtask {subtask_id} failed: {e}\nTraceback:\n{traceback.format_exc()}"
            logger.error(error_details)
            context.task_store.update_subtask_status(subtask_id, "failed")
            context.task_store.log_event(task_id, "subtask_failed", error_details, subtask_id)
            event_bus.publish_sync({
                "type": "subtask_failed",
                "task_id": task_id,
                "subtask_id": subtask_id,
                "error": str(e),
                "timestamp": get_utc_timestamp(),
            })
            raise

    def _build_context_for_subtask(self, task_id: str, subtask: Dict, context: StateContext) -> str:
        task = context.task_store.get_task(task_id)
        lines = [f"Overall task goal: {task['goal']}", ""]

        input_context = subtask.get("input_context")
        if isinstance(input_context, dict):
            context_keys = input_context.get("context_keys", [])
        else:
            if input_context is not None:
                logger.warning(f"Unexpected input_context type for subtask {subtask.get('id')}: {type(input_context)}")
            context_keys = []

        for key in context_keys:
            value = context.task_store.get_context(task_id, key)
            if value:
                lines.append(f"Context key '{key}': {value}")
        if context_keys:
            lines.append("")

        depends_on = subtask.get("depends_on", [])
        if depends_on:
            lines.append("Outputs from dependencies:")
            for dep_id in depends_on:
                try:
                    dep = context.task_store.get_subtask(dep_id)
                    if dep and dep.get("output"):
                        lines.append(f"\nOutput from {dep.get('agent_id', 'unknown')}:")
                        lines.append(dep["output"])
                except Exception as e:
                    logger.warning(f"Could not fetch dependency {dep_id}: {e}")
            lines.append("")

        return "\n".join(lines)
