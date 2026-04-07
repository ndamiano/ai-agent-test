"""
MaestroAgent: the LLM-powered orchestrator.

Maestro owns a task from receipt to delivery. It plans, executes waves of
agent work, evaluates outputs, re-plans as needed, and hands off to a
synthesis agent for the final deliverable.

Uses a finite state machine (FSM) with explicit states:
PLANNING → EXECUTING → VALIDATING → (loop or COMPILING) → FINISHED
"""

import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Callable, Dict, Optional

from config.time_utils import get_utc_timestamp

from agents.agent_store import agent_store
from agents.main_agent import MainAgent
from database.task_store import TaskStore
from tools.execution_context import execution_context

logger = logging.getLogger(__name__)


class MaestroAgent:
    """
    LLM-powered orchestrator that coordinates all agents to accomplish a goal.

    Lifecycle per task:
      1. build_system_prompt()  — inject agent roster into Maestro's identity
      2. plan()                 — first LLM call; Maestro spawns initial wave via tools
      3. run_loop()             — execute ready subtasks, evaluate, re-plan, repeat
      4. synthesize()           — hand off to synthesis agent, return final output
    """

    SYNTHESIS_AGENT_ID = "summarizer"

    DEFAULT_AGENT_ID = "worker"

    def __init__(self, broadcast_fn: Optional[Callable] = None):
        self.task_store = TaskStore()
        self.agent_store = agent_store
        self.broadcast_fn = broadcast_fn
        self.logger = logging.getLogger(__name__)

        import os
        max_workers = int(os.getenv("MAX_PARALLEL_WORKERS", "2"))
        self._executor = ThreadPoolExecutor(max_workers=max_workers)

    # -------------------------------------------------------------------------
    # Public entry point
    # -------------------------------------------------------------------------

    def run(self, task_id: str, broadcast_fn: Optional[Callable] = None) -> str:
        """
        Run a task end-to-end using FSM. Blocks until complete.

        Args:
            task_id:      The task ID (already created in task_store).
            broadcast_fn: Optional WebSocket broadcast callback.

        Returns:
            The final synthesized output string.
        """
        import asyncio
        return asyncio.run(self.run_async(task_id, broadcast_fn))

    async def run_async(self, task_id: str, broadcast_fn: Optional[Callable] = None) -> str:
        """
        Run a task end-to-end using async FSM. Non-blocking.

        Uses a finite state machine with explicit states:
        PLANNING → EXECUTING → VALIDATING → (PLANNING or COMPILING) → FINISHED

        Args:
            task_id:      The task ID (already created in task_store).
            broadcast_fn: Optional WebSocket broadcast callback.

        Returns:
            The final synthesized output string.
        """
        broadcast_fn = broadcast_fn or self.broadcast_fn

        try:
            self.task_store.update_task_status(task_id, "planning")

            # Broadcast task status with full task object (expected by frontend)
            task = self.task_store.get_task(task_id)
            self._broadcast(broadcast_fn, {
                "type": "task_status",
                "task_id": task_id,
                "task": task,
            })

            # Initialize FSM
            from agents.fsm.state_node import StateContext
            from agents.fsm.planning_node import PlanningNode
            from agents.fsm.finished_node import FinishedNode

            context = StateContext(
                task_id=task_id,
                broadcast_fn=broadcast_fn,
                task_store=self.task_store,
                agent_store=self.agent_store,
            )

            # FSM execution loop (async, non-blocking)
            node = PlanningNode()
            first_transition = True
            while not isinstance(node, FinishedNode):
                node = await node.execute_async(context)

                # After planning completes (first transition), emit in_progress status
                if first_transition:
                    first_transition = False
                    self.task_store.update_task_status(task_id, "in_progress")
                    task = self.task_store.get_task(task_id)
                    self._broadcast(broadcast_fn, {
                        "type": "task_status",
                        "task_id": task_id,
                        "task": task,
                    })

            # Task complete
            self.task_store.update_task_status(task_id, "completed")
            self._broadcast(broadcast_fn, {
                "type": "task_completed",
                "task_id": task_id,
            })

            return context.final_output

        except Exception as e:
            logger.error(f"Maestro: task {task_id} failed: {e}")
            self.task_store.update_task_status(task_id, "failed")
            self.task_store.log_event(task_id, "task_failed", f"Maestro error: {e}")
            self._broadcast(broadcast_fn, {
                "type": "task_failed",
                "task_id": task_id,
                "error": str(e),
            })
            raise

    def run_background(self, task_id: str, broadcast_fn: Optional[Callable] = None) -> None:
        """Launch run() in a background thread. Returns immediately."""
        def _run():
            try:
                self.run(task_id, broadcast_fn)
            except Exception:
                pass  # already handled and broadcast inside run()
            finally:
                from database.task_store import close_connection
                close_connection()

        thread = threading.Thread(target=_run, daemon=True)
        thread.start()

    # -------------------------------------------------------------------------
    # Helpers
    # -------------------------------------------------------------------------

    @staticmethod
    def _broadcast(broadcast_fn: Optional[Callable], event: Dict) -> None:
        if broadcast_fn:
            try:
                broadcast_fn(event)
            except Exception as e:
                logger.warning(f"Broadcast failed: {e}")
