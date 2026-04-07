import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Callable, Dict, Optional

from config.time_utils import get_utc_timestamp

from agents.agent_store import agent_store
from agents.main_agent import MainAgent
from database.task_store import task_store
from tools.execution_context import execution_context

logger = logging.getLogger(__name__)


class MaestroAgent:
    """LLM-powered orchestrator that coordinates all agents to accomplish a goal."""

    def __init__(self, broadcast_fn: Optional[Callable] = None):
        self.task_store = task_store
        self.agent_store = agent_store
        self.broadcast_fn = broadcast_fn
        self.logger = logging.getLogger(__name__)

        import os
        max_workers = int(os.getenv("MAX_PARALLEL_WORKERS", "2"))
        self._executor = ThreadPoolExecutor(max_workers=max_workers)

    def run_background(self, task_id: str, broadcast_fn: Optional[Callable] = None) -> None:
        """Launch task in a background thread. Returns immediately."""
        async def _run_async():
            broadcast_fn_ = broadcast_fn or self.broadcast_fn

            try:
                self.task_store.update_task_status(task_id, "planning")
                task = self.task_store.get_task(task_id)
                self._broadcast(broadcast_fn_, {
                    "type": "task_status",
                    "task_id": task_id,
                    "task": task,
                })

                from agents.fsm.state_node import StateContext
                from agents.fsm.planning_node import PlanningNode
                from agents.fsm.finished_node import FinishedNode

                context = StateContext(
                    task_id=task_id,
                    broadcast_fn=broadcast_fn_,
                    task_store=self.task_store,
                    agent_store=self.agent_store,
                )

                node = PlanningNode()
                first_transition = True
                while not isinstance(node, FinishedNode):
                    node = await node.execute_async(context)

                    if first_transition:
                        first_transition = False
                        self.task_store.update_task_status(task_id, "in_progress")
                        task = self.task_store.get_task(task_id)
                        self._broadcast(broadcast_fn_, {
                            "type": "task_status",
                            "task_id": task_id,
                            "task": task,
                        })

                self.task_store.update_task_status(task_id, "completed")
                self._broadcast(broadcast_fn_, {
                    "type": "task_completed",
                    "task_id": task_id,
                })

            except Exception as e:
                logger.error(f"Maestro: task {task_id} failed: {e}")
                self.task_store.update_task_status(task_id, "failed")
                self.task_store.log_event(task_id, "task_failed", f"Maestro error: {e}")
                self._broadcast(broadcast_fn_, {
                    "type": "task_failed",
                    "task_id": task_id,
                    "error": str(e),
                })

        def _run():
            try:
                import asyncio
                asyncio.run(_run_async())
            finally:
                from database.task_store import close_connection
                close_connection()

        thread = threading.Thread(target=_run, daemon=True)
        thread.start()

    @staticmethod
    def _broadcast(broadcast_fn: Optional[Callable], event: Dict) -> None:
        if broadcast_fn:
            try:
                broadcast_fn(event)
            except Exception as e:
                logger.warning(f"Broadcast failed: {e}")
