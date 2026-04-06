"""
Validating state node: Maestro validates quality and checks goal completion.
"""

import logging
from datetime import datetime
from agents.fsm.state_node import StateNode, StateContext
from config.time_utils import get_utc_timestamp

logger = logging.getLogger(__name__)


class ValidatingNode(StateNode):
    """
    VALIDATING state: Maestro validates execution quality and checks goal completion.

    Entry: A wave of subtasks has completed
    Exit: Maestro has evaluated outputs and decided next action
    Transitions:
        - PlanningNode if work quality is bad OR goal not complete
        - CompilingNode if quality good AND goal complete
    """

    @property
    def state_name(self) -> str:
        return "VALIDATING"

    async def execute_async(self, context: StateContext) -> StateNode:
        """Validate outputs and decide next state."""
        from agents.fsm.planning_node import PlanningNode
        from agents.fsm.compiling_node import CompilingNode

        logger.info(f"Maestro: task {context.task_id} — wave {context.wave_count} — VALIDATING")

        # Check for pending/in-progress subtasks
        subtasks = context.task_store.get_subtasks_for_task(context.task_id)
        pending = [s for s in subtasks if s["status"] in ("pending", "in_progress")]
        failed = [s for s in subtasks if s["status"] == "failed"]

        # Handle failures (minimal error handling)
        if failed:
            logger.warning(f"Task {context.task_id} has {len(failed)} failed subtasks")
            # Let Maestro decide what to do about failures
            more_work = await self._maestro_turn(context, phase="error_recovery")
            if more_work:
                return PlanningNode()
            else:
                # Maestro decided to proceed despite failures
                return CompilingNode()

        if pending:
            # Shouldn't happen after wave completes, but guard anyway
            logger.warning(f"Task {context.task_id} still has pending subtasks after wave completion")
            # Re-execute to finish pending work
            from agents.fsm.executing_node import ExecutingNode
            return ExecutingNode()

        # Ask Maestro to evaluate outputs and decide if more work is needed
        more_work = await self._maestro_turn(context, phase="evaluation")

        if more_work:
            # Maestro spawned more subtasks → go back to PLANNING
            return PlanningNode()
        else:
            # Maestro decided we're done → proceed to COMPILING
            return CompilingNode()

    async def _maestro_turn(self, context: StateContext, phase: str) -> bool:
        """
        Make one Maestro LLM call to evaluate state.

        Returns:
            True if Maestro spawned new subtasks (more work),
            False if no new subtasks (ready to finish)
        """
        # Snapshot subtask count before turn
        count_before = len(context.task_store.get_subtasks_for_task(context.task_id))

        # Build prompt
        prompt = self._build_maestro_prompt(context, phase)

        # Get Maestro agent config
        agent_data = context.agent_store.get("maestro")
        rendered_system_prompt = agent_data["system_prompt"].replace(
            "{{AGENT_ROSTER}}", self._build_agent_roster(context)
        )

        # Instantiate and execute Maestro
        from agents.main_agent import MainAgent
        maestro = MainAgent(agent_id="maestro", system_prompt=rendered_system_prompt)
        maestro.set_broadcast_context(context.task_id, "maestro", context.broadcast_fn)

        # Get task working directory
        task = context.task_store.get_task(context.task_id)
        if task is None:
            raise RuntimeError(f"Task {context.task_id} not found in task_store")
        working_directory = task.get("working_directory")

        self._broadcast(context, {
            "type": "agent_message",
            "task_id": context.task_id,
            "agent_id": "maestro",
            "phase": phase,
            "message": f"Maestro evaluating ({phase})",
            "timestamp": get_utc_timestamp(),
        })

        # Execute within execution context
        from tools.execution_context import execution_context
        with execution_context(task_id=context.task_id, subtask_id="maestro", working_directory=working_directory):
            response = maestro.chat(prompt)

        # Ensure response is never None
        if response is None:
            response = ""
            logger.warning(f"Maestro returned None response for task {context.task_id} phase {phase}")

        # Store Maestro's reasoning
        context.task_store.write_context(
            context.task_id,
            key=f"maestro_{phase}_{datetime.now().strftime('%H%M%S')}",
            value=response,
        )
        context.task_store.log_event(
            context.task_id, "agent_message", f"Maestro ({phase}): {response[:200]}"
        )

        # Check if new subtasks were spawned
        count_after = len(context.task_store.get_subtasks_for_task(context.task_id))
        return count_after > count_before

    def _build_maestro_prompt(self, context: StateContext, phase: str) -> str:
        """Build the situational prompt Maestro receives."""
        task = context.task_store.get_task(context.task_id)
        if task is None:
            raise RuntimeError(f"Task {context.task_id} not found in task_store")
        subtasks = context.task_store.get_subtasks_for_task(context.task_id)
        all_context = context.task_store.get_all_context(context.task_id)

        lines = [
            f"TASK ID: {context.task_id}",
            f"ORIGINAL GOAL: {task['goal']}",
            f"CURRENT PHASE: {phase}",
            "",
        ]

        # Subtask status summary
        lines.append("SUBTASK STATUS:")
        if not subtasks:
            lines.append("  No subtasks yet.")
        else:
            for s in subtasks:
                dep_str = f" (depends on: {s['depends_on']})" if s.get("depends_on") else ""
                name_desc = ""
                n, d = s.get("name"), s.get("description")
                if n or d:
                    name_desc = f" {n or ''}" + (f" — {d}" if d else "") + " |"
                lines.append(
                    f"  [{s['status'].upper()}] {s['id'][:8]} |{name_desc} "
                    f"agent={s['agent_id']} | pos={s['position']}{dep_str}"
                )
                if s.get("output"):
                    preview = s["output"][:120].replace("\n", " ")
                    lines.append(f"    Output preview: {preview}...")
        lines.append("")

        # Full context store
        lines.append("CONTEXT STORE (all outputs and notes):")
        if not all_context:
            lines.append("  Empty.")
        else:
            for key, value in all_context.items():
                lines.append(f"\n  [{key}]")
                lines.append(f"  {value[:500]}{'...' if len(value) > 500 else ''}")
        lines.append("")

        # Phase-specific instruction
        if phase == "evaluation":
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

    def _build_agent_roster(self, context: StateContext) -> str:
        """Format the agent roster for injection into Maestro's system prompt."""
        agents = context.agent_store.list()
        lines = []
        for a in agents:
            if a.get("id") == "maestro":
                continue
            tools = ", ".join(a.get("tools", [])) or "none"
            lines.append(f"- {a['id']}: {a['name']}")
            lines.append(f"  {a['description']}")
            lines.append(f"  Tools: {tools}")
        return "\n".join(lines)
