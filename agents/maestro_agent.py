"""
MaestroAgent: the LLM-powered orchestrator.

Maestro owns a task from receipt to delivery. It plans, executes waves of
agent work, evaluates outputs, re-plans as needed, and hands off to a
synthesis agent for the final deliverable.

Replaces the static PlannerAgent + Orchestrator chain.
"""

import json
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Callable, Dict, List, Optional

from config.time_utils import get_utc_timestamp

from agents.agent_store import AgentStore
from agents.agent_lifecycle import AgentLifecycleManager
from agents.context_builder import ContextBuilder
from agents.main_agent import MainAgent
from database.task_store import TaskStore
from tools.logging_utils import tool_logger
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

    SYNTHESIS_AGENT_ID = "synthesizer"

    def __init__(self, broadcast_fn: Optional[Callable] = None):
        self.task_store = TaskStore()
        self.agent_store = AgentStore()
        self.context_builder = ContextBuilder(self.task_store)
        self.lifecycle_manager = AgentLifecycleManager(self.agent_store, self.task_store)
        self.broadcast_fn = broadcast_fn
        self.logger = tool_logger

        import os
        max_workers = int(os.getenv("MAX_PARALLEL_WORKERS", "2"))
        self._executor = ThreadPoolExecutor(max_workers=max_workers)

    # -------------------------------------------------------------------------
    # Public entry point
    # -------------------------------------------------------------------------

    def run(self, task_id: str, broadcast_fn: Optional[Callable] = None) -> str:
        """
        Run a task end-to-end. Blocks until complete.

        Args:
            task_id:      The task ID (already created in task_store).
            broadcast_fn: Optional WebSocket broadcast callback.

        Returns:
            The final synthesized output string.
        """
        broadcast_fn = broadcast_fn or self.broadcast_fn
        self._broadcast(broadcast_fn, {
            "type": "task_status",
            "task_id": task_id,
            "status": "planning",
        })

        try:
            self.task_store.update_task_status(task_id, "planning")

            # --- Step 1: Initial planning ---
            # Maestro makes its first LLM call. Its tools include spawn_task,
            # so it will call spawn_task one or more times to seed the plan.
            self._maestro_turn(task_id, broadcast_fn, phase="planning")

            self.task_store.update_task_status(task_id, "in_progress")
            self._broadcast(broadcast_fn, {
                "type": "task_status",
                "task_id": task_id,
                "status": "in_progress",
            })

            # --- Step 2: Execute → evaluate → re-plan loop ---
            final_output = self._run_loop(task_id, broadcast_fn)

            # --- Step 3: Mark complete ---
            self.task_store.update_task_status(task_id, "completed")
            self._broadcast(broadcast_fn, {
                "type": "task_completed",
                "task_id": task_id,
            })

            # --- Step 4: Cleanup temporary agents ---
            cleanup_count = self.lifecycle_manager.cleanup_task_temporary_agents(task_id)
            if cleanup_count > 0:
                logger.info(f"Cleaned up {cleanup_count} temporary agent(s) for task {task_id}")

            return final_output

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
    # Core loop
    # -------------------------------------------------------------------------

    def _run_loop(self, task_id: str, broadcast_fn: Optional[Callable]) -> str:
        """
        Execute subtasks in waves. After each wave completes, Maestro
        evaluates outputs and either spawns more work or triggers synthesis.

        Returns the final synthesized output.
        """
        max_waves = 20  # safety valve
        wave = 0
        while wave < max_waves:
            wave += 1
            logger.info(f"Maestro: task {task_id} — wave {wave}")

            # Execute all currently ready subtasks
            completed_any = self._execute_wave(task_id, broadcast_fn)

            # Check if there are still subtasks in flight or pending
            subtasks = self.task_store.get_subtasks_for_task(task_id)
            pending = [s for s in subtasks if s["status"] in ("pending", "in_progress")]
            failed  = [s for s in subtasks if s["status"] == "failed"]

            if failed:
                # Let Maestro decide what to do about failures
                self._maestro_turn(task_id, broadcast_fn, phase="error_recovery")
                continue

            if pending:
                # More subtasks exist but aren't ready yet — something is still running.
                # This shouldn't happen (we wait for the wave to finish) but guard anyway.
                continue

            # Queue is empty — ask Maestro to evaluate and decide next step.
            # Maestro will either call spawn_task (more work) or not (done).
            more_work = self._maestro_turn(task_id, broadcast_fn, phase="evaluation")

            if not more_work:
                # Maestro decided we're done — run synthesis
                return self._synthesize(task_id, broadcast_fn)

        raise RuntimeError(f"Task {task_id} exceeded maximum wave limit ({max_waves})")

    def _execute_wave(self, task_id: str, broadcast_fn: Optional[Callable]) -> bool:
        """
        Execute all currently ready subtasks in parallel.
        Blocks until every submitted subtask either completes or fails.

        Returns True if at least one subtask was executed.
        """
        ready = self.task_store.get_ready_subtasks(task_id)
        if not ready:
            return False

        futures = {
            self._executor.submit(self._execute_subtask, s, broadcast_fn): s
            for s in ready
        }

        for future in as_completed(futures):
            subtask = futures[future]
            try:
                future.result()
            except Exception as e:
                logger.error(f"Maestro: subtask {subtask['id']} failed: {e}")
                # Status already set to failed inside _execute_subtask

        return True

    # -------------------------------------------------------------------------
    # Maestro LLM turn
    # -------------------------------------------------------------------------

    def _maestro_turn(
        self,
        task_id: str,
        broadcast_fn: Optional[Callable],
        phase: str = "evaluation",
    ) -> bool:
        """
        Make one Maestro LLM call. Maestro receives the full context and
        task status, then decides what to do next via tool calls.

        Returns True if Maestro spawned any new subtasks (more work to do),
        False if it made no tool calls (signals completion / ready to synthesize).
        """
        # Snapshot total subtask count before the turn so we can detect
        # whether Maestro actually spawned anything new vs. just evaluating.
        count_before = len(self.task_store.get_subtasks_for_task(task_id))

        prompt = self._build_maestro_prompt(task_id, phase)

        # Render the agent roster into Maestro's system prompt
        agent_data = self.agent_store.get("maestro")
        rendered_system_prompt = agent_data["system_prompt"].replace(
            "{{AGENT_ROSTER}}", self._build_agent_roster()
        )
        maestro = MainAgent(agent_id="maestro", system_prompt=rendered_system_prompt)

        # Set broadcast context for maestro
        maestro.set_broadcast_context(task_id, "maestro", broadcast_fn)

        self._broadcast(broadcast_fn, {
            "type": "agent_message",
            "task_id": task_id,
            "agent_id": "maestro",
            "phase": phase,
            "message": f"Maestro evaluating ({phase})",
            "timestamp": get_utc_timestamp(),
        })

        # Execute within execution context
        with execution_context(task_id=task_id, subtask_id="maestro"):
            response = maestro.chat(prompt)

        # Ensure response is never None
        if response is None:
            response = ""
            logger.warning(f"Maestro returned None response for task {task_id} phase {phase}")

        # Write Maestro's reasoning into the context store for traceability
        self.task_store.write_context(
            task_id,
            key=f"maestro_{phase}_{datetime.now().strftime('%H%M%S')}",
            value=response,
        )
        self.task_store.log_event(
            task_id, "agent_message", f"Maestro ({phase}): {response[:200]}"
        )

        # Spawned new = total subtask count increased during this turn.
        # Checking pending-only was wrong: a pending synthesizer from a prior
        # spawn would make the loop think there was always more work to do,
        # burning through all 20 waves before hitting the limit.
        count_after = len(self.task_store.get_subtasks_for_task(task_id))
        return count_after > count_before

    def _build_maestro_prompt(self, task_id: str, phase: str) -> str:
        """
        Build the situational prompt Maestro receives at each turn.
        Injects: original goal, all context store output, subtask status summary.
        """
        task = self.task_store.get_task(task_id)
        subtasks = self.task_store.get_subtasks_for_task(task_id)
        all_context = self.task_store.get_all_context(task_id)

        # --- Goal ---
        lines = [
            f"TASK ID: {task_id}",
            f"ORIGINAL GOAL: {task['goal']}",
            f"CURRENT PHASE: {phase}",
            "",
        ]

        # --- Subtask status summary ---
        lines.append("SUBTASK STATUS:")
        if not subtasks:
            lines.append("  No subtasks yet.")
        else:
            for s in subtasks:
                dep_str = f" (depends on: {s['depends_on']})" if s.get("depends_on") else ""
                lines.append(
                    f"  [{s['status'].upper()}] {s['id'][:8]} | "
                    f"agent={s['agent_id']} | pos={s['position']}{dep_str}"
                )
                if s.get("output"):
                    preview = s["output"][:120].replace("\n", " ")
                    lines.append(f"    Output preview: {preview}...")
        lines.append("")

        # --- Full context store ---
        lines.append("CONTEXT STORE (all outputs and notes):")
        if not all_context:
            lines.append("  Empty.")
        else:
            for key, value in all_context.items():
                lines.append(f"\n  [{key}]")
                lines.append(f"  {value[:500]}{'...' if len(value) > 500 else ''}")
        lines.append("")

        # --- Phase-specific instruction ---
        if phase == "planning":
            lines.append(
                "INSTRUCTION: This is the initial planning phase. "
                "Review the goal and spawn the first wave of subtasks. "
                "Only plan what you can plan now — you will re-evaluate after each wave completes."
            )
        elif phase == "evaluation":
            lines.append(
                "INSTRUCTION: A wave of subtasks has completed. "
                "Review the outputs above. If the goal is not yet achieved and more work is needed, "
                "spawn the next wave of subtasks. "
                "If all work is complete and ready for synthesis, do NOT call spawn_task — "
                "simply respond confirming the work is done."
            )
        elif phase == "error_recovery":
            lines.append(
                "INSTRUCTION: One or more subtasks have failed. "
                "Review the failures above and decide how to proceed: "
                "retry the failed subtask, spawn an alternative, or acknowledge the failure and continue."
            )

        return "\n".join(lines)

    # -------------------------------------------------------------------------
    # Synthesis
    # -------------------------------------------------------------------------

    def _synthesize(self, task_id: str, broadcast_fn: Optional[Callable]) -> str:
        """
        Hand off to the synthesis agent to produce the final deliverable.
        Falls back to assembling raw context if no synthesizer agent exists.
        """
        self._broadcast(broadcast_fn, {
            "type": "agent_message",
            "task_id": task_id,
            "agent_id": self.SYNTHESIS_AGENT_ID,
            "message": "Synthesizing final output",
            "timestamp": get_utc_timestamp(),
        })

        if not self.agent_store.exists(self.SYNTHESIS_AGENT_ID):
            logger.warning(
                "MaestroAgent: no 'synthesizer' agent found — "
                "returning raw context assembly as final output."
            )
            return self._fallback_synthesis(task_id)

        all_context = self.task_store.get_all_context(task_id)
        task = self.task_store.get_task(task_id)

        context_dump = "\n\n".join(
            f"[{key}]\n{value}" for key, value in all_context.items()
        )
        prompt = (
            f"ORIGINAL GOAL: {task['goal']}\n\n"
            f"ALL PRODUCED CONTENT:\n{context_dump}\n\n"
            "Synthesize the above into the final deliverable. "
            "Follow the format appropriate for the goal. "
            "Output only the deliverable — no meta-commentary."
        )

        synthesizer = MainAgent(agent_id=self.SYNTHESIS_AGENT_ID)
        output = synthesizer.chat(prompt)

        self.task_store.write_context(task_id, "final_output", output)
        self.task_store.log_event(task_id, "task_completed", "Synthesis complete")

        return output

    def _fallback_synthesis(self, task_id: str) -> str:
        """Assemble raw outputs when no synthesizer agent is available."""
        subtasks = self.task_store.get_subtasks_for_task(task_id)
        parts = []
        for s in subtasks:
            if s.get("output"):
                parts.append(f"=== {s['agent_id']} (subtask {s['id'][:8]}) ===\n{s['output']}")
        return "\n\n".join(parts) if parts else "No output produced."

    # -------------------------------------------------------------------------
    # Subtask execution
    # -------------------------------------------------------------------------

    def _execute_subtask(
        self, subtask: Dict, broadcast_fn: Optional[Callable]
    ) -> str:
        """Execute a single subtask. Mirrors the existing Orchestrator pattern."""
        subtask_id = subtask["id"]
        task_id = subtask["task_id"]
        agent_id = subtask["agent_id"]
        start_time = time.time()

        try:
            self.task_store.update_subtask_status(subtask_id, "in_progress")
            self._broadcast(broadcast_fn, {
                "type": "subtask_started",
                "task_id": task_id,
                "subtask_id": subtask_id,
                "agent_id": agent_id,
                "timestamp": get_utc_timestamp(),
            })

            fresh = self.task_store.get_subtask(subtask_id)
            context_text = self.context_builder.build_for_subtask(task_id, fresh)
            agent = MainAgent(agent_id=fresh["agent_id"])

            # Set broadcast context AND execution context
            agent.set_broadcast_context(task_id, subtask_id, broadcast_fn)

            message = f"{context_text}\n\nTask: {fresh['goal']}"

            # Execute within execution context
            with execution_context(task_id=task_id, subtask_id=subtask_id):
                output = agent.chat(message)

            self.task_store.set_subtask_output(subtask_id, output)
            self._broadcast(broadcast_fn, {
                "type": "subtask_completed",
                "task_id": task_id,
                "subtask_id": subtask_id,
                "timestamp": get_utc_timestamp(),
            })

            # Track successful agent usage
            execution_time_ms = int((time.time() - start_time) * 1000)
            self.lifecycle_manager.register_agent_usage(
                agent_id, task_id, subtask_id, success=True, execution_time_ms=execution_time_ms
            )

            return output

        except Exception as e:
            # Track failed agent usage
            execution_time_ms = int((time.time() - start_time) * 1000)
            self.lifecycle_manager.register_agent_usage(
                agent_id, task_id, subtask_id, success=False, execution_time_ms=execution_time_ms
            )

            self.task_store.update_subtask_status(subtask_id, "failed")
            self.task_store.log_event(
                task_id, "subtask_failed", f"Subtask {subtask_id} failed: {e}", subtask_id
            )
            self._broadcast(broadcast_fn, {
                "type": "subtask_failed",
                "task_id": task_id,
                "subtask_id": subtask_id,
                "error": str(e),
                "timestamp": get_utc_timestamp(),
            })
            raise

    # -------------------------------------------------------------------------
    # Helpers
    # -------------------------------------------------------------------------

    def _build_agent_roster(self) -> str:
        """Format the agent roster for injection into Maestro's system prompt."""
        agents = self.agent_store.list()
        lines = []
        for a in agents:
            if a.get("id") == "maestro":
                continue
            tools = ", ".join(a.get("tools", [])) or "none"
            lines.append(f"- {a['id']}: {a['name']}")
            lines.append(f"  {a['description']}")
            lines.append(f"  Tools: {tools}")
        return "\n".join(lines)

    @staticmethod
    def _broadcast(broadcast_fn: Optional[Callable], event: Dict) -> None:
        if broadcast_fn:
            try:
                broadcast_fn(event)
            except Exception as e:
                logger.warning(f"Broadcast failed: {e}")