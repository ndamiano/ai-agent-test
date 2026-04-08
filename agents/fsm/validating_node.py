"""
Validating state node: Maestro validates quality and checks goal completion.
"""

import logging
import json
from datetime import datetime
from agents.fsm.state_node import StateNode, StateContext
from config.time_utils import get_utc_timestamp
from api.websocket.event_bus import event_bus

logger = logging.getLogger(__name__)


class ValidatingNode(StateNode):
    """VALIDATING state: Maestro validates execution quality and checks goal completion."""

    @property
    def state_name(self) -> str:
        return "VALIDATING"

    async def execute_async(self, context: StateContext) -> StateNode:
        """Validate outputs and decide next state."""
        from agents.fsm.planning_node import PlanningNode
        from agents.fsm.compiling_node import CompilingNode

        logger.info(f"Maestro: task {context.task_id} — wave {context.wave_count} — VALIDATING")

        subtasks = context.task_store.get_subtasks_for_task(context.task_id)
        pending = [s for s in subtasks if s["status"] in ("pending", "in_progress")]
        failed = [s for s in subtasks if s["status"] == "failed"]

        if failed:
            logger.warning(f"Task {context.task_id} has {len(failed)} failed subtasks")
            return PlanningNode()

        if pending:
            logger.warning(f"Task {context.task_id} still has pending subtasks after wave completion")
            from agents.fsm.executing_node import ExecutingNode
            return ExecutingNode()

        is_complete = await self._maestro_turn(context, phase="evaluation")

        if is_complete:
            return CompilingNode()
        else:
            return PlanningNode()

    async def _maestro_turn(self, context: StateContext, phase: str) -> bool:
        """Make one Maestro LLM call to evaluate state."""
        prompt = self._build_maestro_prompt(context, phase)

        agent_data = context.agent_store.get("validator")
        rendered_system_prompt = agent_data["system_prompt"].replace(
            "{{AGENT_ROSTER}}", self._build_agent_roster(context)
        )

        from agents.main_agent import MainAgent
        validator = MainAgent(agent_id="validator", system_prompt=rendered_system_prompt)

        task = context.task_store.get_task(context.task_id)
        if task is None:
            raise RuntimeError(f"Task {context.task_id} not found in task_store")
        working_directory = task.get("working_directory")

        event_bus.publish_sync({
            "type": "agent_message",
            "task_id": context.task_id,
            "agent_id": "validator",
            "phase": phase,
            "message": f"Validator evaluating ({phase})",
            "timestamp": get_utc_timestamp(),
        })

        from tools.execution_context import execution_context
        with execution_context(task_id=context.task_id, subtask_id="validator", working_directory=working_directory):
            response = validator.chat(prompt)

        if response is None:
            response = ""
            logger.warning(f"Validator returned None response for task {context.task_id} phase {phase}")

        context.task_store.write_context(
            context.task_id,
            key=f"validator_{phase}_{datetime.now().strftime('%H%M%S')}",
            value=response,
        )
        context.task_store.log_event(
            context.task_id, "agent_message", f"Validator ({phase}): {response[:200]}"
        )

        verdict = None
        for msg in reversed(validator.get_message_history()):
            if msg.get("role") == "tool":
                try:
                    result = json.loads(msg["content"])
                    if "verdict" in result:
                        verdict = result["verdict"]
                        break
                except (json.JSONDecodeError, KeyError):
                    continue

        if verdict is None:
            logger.warning(f"Validator did not call submit_verdict for task {context.task_id}, defaulting to INCOMPLETE")
            return False

        return verdict == "COMPLETE"

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

        lines.append("CONTEXT STORE (all outputs and notes):")
        if not all_context:
            lines.append("  Empty.")
        else:
            for key, value in all_context.items():
                lines.append(f"\n  [{key}]")
                lines.append(f"  {value[:500]}{'...' if len(value) > 500 else ''}")
        lines.append("")

        if phase == "evaluation":
            lines.append(
                "INSTRUCTION: Review the completed subtask outputs above against the ORIGINAL GOAL. "
                "Your job is verification only — you cannot create or spawn any new tasks.\n\n"
                "Assess whether the work fully satisfies the original goal. Then respond with:\n"
                "- The word COMPLETE if the goal has been fully achieved and is ready for synthesis.\n"
                "- The word INCOMPLETE if the goal has not been fully achieved, followed by a brief explanation of what is missing.\n\n"
                "Do not suggest next steps. Do not describe what you would do. Just verdict and justification."
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
