"""
Compiling state node: Run synthesis agent to create final output.
"""

import logging
import json
from pathlib import Path
from agents.fsm.state_node import StateNode, StateContext
from config.time_utils import get_utc_timestamp

logger = logging.getLogger(__name__)


class CompilingNode(StateNode):
    """
    COMPILING state: Run synthesis agent to create final output.

    Entry: Maestro has validated all work is complete
    Exit: Synthesis agent has produced final manifest
    Transition: Always → FinishedNode
    """

    @property
    def state_name(self) -> str:
        return "COMPILING"

    async def execute_async(self, context: StateContext) -> StateNode:
        """Run synthesis and transition to finished."""
        from agents.fsm.finished_node import FinishedNode

        # Broadcast state entry
        self._broadcast(context, {
            "type": "fsm_state_change",
            "task_id": context.task_id,
            "from_state": "VALIDATING",
            "to_state": self.state_name,
            "timestamp": get_utc_timestamp(),
        })

        logger.info(f"Maestro: task {context.task_id} — COMPILING final output")

        # Run synthesis
        final_output = await self._synthesize(context)

        # Store final output in context for main loop to return
        context.final_output = final_output

        # Transition to FINISHED
        return FinishedNode()

    async def _synthesize(self, context: StateContext) -> str:
        """
        Hand off to the summarizer agent to produce final output.
        Falls back to basic manifest if no summarizer exists.
        """
        SYNTHESIS_AGENT_ID = "summarizer"

        self._broadcast(context, {
            "type": "agent_message",
            "task_id": context.task_id,
            "agent_id": SYNTHESIS_AGENT_ID,
            "message": "Summarizing results",
            "timestamp": get_utc_timestamp(),
        })

        if not context.agent_store.exists(SYNTHESIS_AGENT_ID):
            logger.warning(
                "CompilingNode: no 'summarizer' agent found — "
                "returning fallback manifest as final output."
            )
            return self._fallback_synthesis(context)

        # Build synthesis prompt
        task = context.task_store.get_task(context.task_id)
        subtasks = context.task_store.get_subtasks_for_task(context.task_id)
        completed = [s for s in subtasks if s["status"] == "completed"]

        prompt = (
            f"ORIGINAL GOAL: {task['goal']}\n\n"
            f"COMPLETED SUBTASKS ({len(completed)}):\n"
        )
        for s in completed:
            preview = (s["output"][:120].replace("\n", " ") + "...") if s.get("output") else "(no output)"
            prompt += f"  - {s['id'][:8]} | agent={s['agent_id']} | {s['goal'][:80]}\n    Preview: {preview}\n"
        prompt += (
            "\nUse list_files and list_subtasks to see what was produced. "
            "Then return a JSON manifest with 'summary' and 'artifacts' fields. "
            "Output only the JSON — no other text."
        )

        # Instantiate and execute summarizer
        from agents.main_agent import MainAgent
        summarizer = MainAgent(agent_id=SYNTHESIS_AGENT_ID)
        summarizer.set_broadcast_context(context.task_id, "summarizer", context.broadcast_fn)

        output = summarizer.chat(prompt)

        # Parse JSON manifest
        try:
            manifest = json.loads(output)
            summary = manifest.get("summary", "")
        except (json.JSONDecodeError, AttributeError):
            # If parsing fails, treat entire output as summary
            summary = output
            manifest = {"summary": summary, "artifacts": []}

        # Store summary and manifest
        context.task_store.write_context(context.task_id, "final_output", summary)
        context.task_store.write_context(context.task_id, "final_manifest", json.dumps(manifest))
        context.task_store.log_event(context.task_id, "task_completed", "Summarization complete")

        return summary

    def _fallback_synthesis(self, context: StateContext) -> str:
        """Return a basic JSON manifest when no summarizer agent is available."""
        subtasks = context.task_store.get_subtasks_for_task(context.task_id)
        completed = [s for s in subtasks if s["status"] == "completed"]
        summary = f"Completed {len(completed)} subtask(s)."

        # Build artifacts from actual files in task's output directory
        artifacts = []
        task_output_dir = Path(f"outputs/{context.task_id}")
        if task_output_dir.exists() and task_output_dir.is_dir():
            for file_path in task_output_dir.rglob("*"):
                if file_path.is_file():
                    # Make path relative to outputs directory
                    relative_path = file_path.relative_to("outputs")
                    artifacts.append({
                        "type": "zip" if file_path.suffix in ['.zip', '.tar', '.gz'] else "file",
                        "label": file_path.name,
                        "path": f"outputs/{relative_path}"
                    })

        manifest = {"summary": summary, "artifacts": artifacts}

        context.task_store.write_context(context.task_id, "final_output", summary)
        context.task_store.write_context(context.task_id, "final_manifest", json.dumps(manifest))

        return summary
