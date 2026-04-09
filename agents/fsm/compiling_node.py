import logging
import json
from pathlib import Path
from agents.fsm.state_node import StateNode, StateContext
from api.websocket.event_bus import event_bus
from config.time_utils import get_utc_timestamp

logger = logging.getLogger(__name__)

SYNTHESIS_AGENT_ID = "summarizer"


class CompilingNode(StateNode):
    @property
    def state_name(self) -> str:
        return "COMPILING"

    async def execute_async(self, context: StateContext) -> StateNode:
        from agents.fsm.finished_node import FinishedNode
        logger.info(f"Maestro: task {context.task_id} — COMPILING final output")
        context.final_output = await self._synthesize(context)
        return FinishedNode()

    async def _synthesize(self, context: StateContext) -> str:
        event_bus.publish_sync({
            "type": "agent_message",
            "task_id": context.task_id,
            "agent_id": SYNTHESIS_AGENT_ID,
            "message": "Summarizing results",
            "timestamp": get_utc_timestamp(),
        })

        if not context.agent_store.exists(SYNTHESIS_AGENT_ID):
            logger.warning("CompilingNode: no 'summarizer' agent found — returning fallback manifest")
            return self._fallback_synthesis(context)

        task = context.task_store.get_task(context.task_id)
        subtasks = context.task_store.get_subtasks_for_task(context.task_id)
        completed = [s for s in subtasks if s["status"] == "completed"]

        prompt = f"ORIGINAL GOAL: {task['goal']}\n\nCOMPLETED SUBTASKS ({len(completed)}):\n"
        for s in completed:
            preview = (s["output"][:120].replace("\n", " ") + "...") if s.get("output") else "(no output)"
            prompt += f"  - {s['id'][:8]} | agent={s['agent_id']} | {s['goal'][:80]}\n    Preview: {preview}\n"
        prompt += (
            "\nUse list_files and list_subtasks to see what was produced. "
            "Then return a JSON manifest with 'summary' and 'artifacts' fields. "
            "Output only the JSON — no other text."
        )

        from agents.main_agent import MainAgent
        output = MainAgent(agent_id=SYNTHESIS_AGENT_ID).chat(prompt)

        try:
            manifest = json.loads(output)
            summary = manifest.get("summary", "")
        except (json.JSONDecodeError, AttributeError):
            summary = output
            manifest = {"summary": summary, "artifacts": []}

        context.task_store.write_context(context.task_id, "final_output", summary)
        context.task_store.write_context(context.task_id, "final_manifest", json.dumps(manifest))
        context.task_store.log_event(context.task_id, "task_completed", "Summarization complete")
        return summary

    def _fallback_synthesis(self, context: StateContext) -> str:
        subtasks = context.task_store.get_subtasks_for_task(context.task_id)
        completed = [s for s in subtasks if s["status"] == "completed"]
        summary = f"Completed {len(completed)} subtask(s)."

        artifacts = []
        task_output_dir = Path(f"outputs/{context.task_id}")
        if task_output_dir.exists() and task_output_dir.is_dir():
            for file_path in task_output_dir.rglob("*"):
                if file_path.is_file():
                    relative_path = file_path.relative_to("outputs")
                    artifacts.append({
                        "type": "zip" if file_path.suffix in ['.zip', '.tar', '.gz'] else "file",
                        "label": file_path.name,
                        "path": f"outputs/{relative_path}",
                    })

        manifest = {"summary": summary, "artifacts": artifacts}
        context.task_store.write_context(context.task_id, "final_output", summary)
        context.task_store.write_context(context.task_id, "final_manifest", json.dumps(manifest))
        return summary
