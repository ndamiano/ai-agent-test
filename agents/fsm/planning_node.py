"""
Planning state node: Maestro analyzes task and spawns subtasks.
"""

import logging
from datetime import datetime
from agents.fsm.state_node import StateNode, StateContext
from config.time_utils import get_utc_timestamp

logger = logging.getLogger(__name__)


class PlanningNode(StateNode):
    """
    PLANNING state: Maestro analyzes the task and spawns initial subtasks.

    Entry: Task is in 'planning' status
    Exit: Maestro has evaluated and possibly spawned subtasks
    Transitions:
        - ExecutingNode if subtasks were spawned
        - CompilingNode if no work was spawned (edge case)
    """

    @property
    def state_name(self) -> str:
        return "PLANNING"

    async def execute_async(self, context: StateContext) -> StateNode:
        """Execute planning: Maestro spawns subtasks."""
        from agents.fsm.executing_node import ExecutingNode
        from agents.fsm.compiling_node import CompilingNode

        logger.info(f"Maestro: task {context.task_id} — PLANNING")

        # Snapshot subtask count before Maestro turn
        count_before = len(context.task_store.get_subtasks_for_task(context.task_id))

        # Build prompt for Maestro
        prompt = self._build_maestro_prompt(context, phase="planning")

        # Get Maestro agent config and render agent roster
        agent_data = context.agent_store.get("maestro")
        rendered_system_prompt = agent_data["system_prompt"].replace(
            "{{AGENT_ROSTER}}", self._build_agent_roster(context)
        )

        # Instantiate and execute Maestro
        from agents.main_agent import MainAgent
        maestro = MainAgent(agent_id="maestro", system_prompt=rendered_system_prompt)

        # Get task working directory for execution context
        task = context.task_store.get_task(context.task_id)
        if task is None:
            raise RuntimeError(f"Task {context.task_id} not found in task_store")
        working_directory = task.get("working_directory")

        # Execute Maestro within execution context
        from tools.execution_context import execution_context
        with execution_context(task_id=context.task_id, subtask_id="maestro", working_directory=working_directory):
            response = maestro.chat(prompt)

        # Ensure response is never None
        if response is None:
            response = ""
            logger.warning(f"Maestro returned None response for task {context.task_id} in planning phase")

        # Store Maestro's reasoning for traceability
        context.task_store.write_context(
            context.task_id,
            key=f"maestro_planning_{datetime.now().strftime('%H%M%S')}",
            value=response,
        )
        context.task_store.log_event(
            context.task_id, "agent_message", f"Maestro (planning): {response[:200]}"
        )

        # Check if subtasks were spawned
        count_after = len(context.task_store.get_subtasks_for_task(context.task_id))
        spawned = count_after > count_before

        if not spawned:
            logger.warning(f"Planning phase completed but no subtasks spawned for task {context.task_id}")
            # No work to do - skip directly to synthesis
            return CompilingNode()

        # Transition to EXECUTING
        return ExecutingNode()

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
        if phase == "planning":
            lines.append(
                "INSTRUCTION: This is the initial planning phase. "
                "Review the goal and spawn the first wave of subtasks. "
                "Only plan what you can plan now — you will re-evaluate after each wave completes."
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
