import json
import logging
from datetime import datetime
from agents.fsm.state_node import StateNode, StateContext
from api.websocket.event_bus import event_bus
from config.time_utils import get_utc_timestamp

logger = logging.getLogger(__name__)


class ValidatingNode(StateNode):
    @property
    def state_name(self) -> str:
        return "VALIDATING"

    async def execute_async(self, context: StateContext) -> StateNode:
        from agents.fsm.planning_node import PlanningNode
        from agents.fsm.compiling_node import CompilingNode

        logger.info(f"Maestro: task {context.task_id} — wave {context.wave_count} — VALIDATING")

        subtasks = context.task_store.get_subtasks_for_task(context.task_id)
        if [s for s in subtasks if s["status"] == "failed"]:
            return PlanningNode()
        if [s for s in subtasks if s["status"] in ("pending", "in_progress")]:
            from agents.fsm.executing_node import ExecutingNode
            return ExecutingNode()

        is_complete = await self._validator_turn(context)
        return CompilingNode() if is_complete else PlanningNode()

    async def _validator_turn(self, context: StateContext) -> bool:
        prompt = self._build_validator_prompt(context)

        agent_data = context.agent_store.get("validator")
        rendered_system_prompt = agent_data["system_prompt"].replace(
            "{{AGENT_ROSTER}}", self._build_agent_roster(context)
        )

        from agents.main_agent import MainAgent
        validator = MainAgent(agent_id="validator", system_prompt=rendered_system_prompt)

        task = context.task_store.get_task(context.task_id)
        working_directory = task.get("working_directory")

        event_bus.publish_sync({
            "type": "agent_message",
            "task_id": context.task_id,
            "agent_id": "validator",
            "phase": "evaluation",
            "message": "Validator evaluating",
            "timestamp": get_utc_timestamp(),
        })

        from tools.execution_context import execution_context
        with execution_context(task_id=context.task_id, subtask_id="validator", working_directory=working_directory):
            response = validator.chat(prompt)

        if response is None:
            response = ""
            logger.warning(f"Validator returned None for task {context.task_id}")

        context.task_store.write_context(
            context.task_id,
            key=f"validator_evaluation_{datetime.now().strftime('%H%M%S')}",
            value=response,
        )
        context.task_store.log_event(context.task_id, "agent_message", f"Validator: {response[:200]}")

        for msg in reversed(validator.get_message_history()):
            if msg.get("role") == "tool":
                try:
                    result = json.loads(msg["content"])
                    if "verdict" in result:
                        return result["verdict"] == "COMPLETE"
                except (json.JSONDecodeError, KeyError):
                    continue

        logger.warning(f"Validator did not call submit_verdict for task {context.task_id}, defaulting to INCOMPLETE")
        return False

    def _build_validator_prompt(self, context: StateContext) -> str:
        task = context.task_store.get_task(context.task_id)
        subtasks = context.task_store.get_subtasks_for_task(context.task_id)
        all_context = context.task_store.get_all_context(context.task_id)

        lines = [
            f"TASK ID: {context.task_id}",
            f"ORIGINAL GOAL: {task['goal']}",
            "",
            "SUBTASK STATUS:",
        ]

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
                    lines.append(f"    Output preview: {s['output'][:120].replace(chr(10), ' ')}...")
        lines.append("")

        lines.append("CONTEXT STORE (all outputs and notes):")
        if not all_context:
            lines.append("  Empty.")
        else:
            for key, value in all_context.items():
                lines.append(f"\n  [{key}]")
                lines.append(f"  {value[:500]}{'...' if len(value) > 500 else ''}")
        lines.append("")

        criteria_raw = context.task_store.get_context(context.task_id, "acceptance_criteria")
        has_criteria = False
        if criteria_raw:
            try:
                criteria = json.loads(criteria_raw)
                lines.append("ACCEPTANCE CRITERIA (must all pass for COMPLETE):")
                for c in criteria:
                    lines.append(f"  [{c['id']}] {c['criterion']}")
                    if c.get("rationale"):
                        lines.append(f"         Rationale: {c['rationale']}")
                lines.append("")
                has_criteria = True
            except Exception:
                pass

        if has_criteria:
            lines.append(
                "INSTRUCTION: Review the completed work against EACH acceptance criterion listed above. "
                "Your job is verification only — you cannot create or spawn any new tasks.\n\n"
                "Every criterion must pass for the verdict to be COMPLETE. "
                "If any criterion is not fully satisfied by the work in the context store, "
                "the verdict is INCOMPLETE.\n\n"
                "Call submit_verdict with your verdict and a justification that names which criteria "
                "passed and which failed (if any). Do not suggest next steps."
            )
        else:
            lines.append(
                "INSTRUCTION: Review the completed subtask outputs above against the ORIGINAL GOAL. "
                "Your job is verification only — you cannot create or spawn any new tasks.\n\n"
                "Call submit_verdict with COMPLETE if the goal has been fully achieved and is ready "
                "for synthesis, or INCOMPLETE with a brief explanation of what is missing.\n\n"
                "Do not suggest next steps. Do not describe what you would do. Just verdict and justification."
            )

        return "\n".join(lines)

    def _build_agent_roster(self, context: StateContext) -> str:
        lines = []
        for a in context.agent_store.list():
            if a.get("id") == "maestro":
                continue
            tools = ", ".join(a.get("tools", [])) or "none"
            lines.append(f"- {a['id']}: {a['name']}")
            lines.append(f"  {a['description']}")
            lines.append(f"  Tools: {tools}")
        return "\n".join(lines)
