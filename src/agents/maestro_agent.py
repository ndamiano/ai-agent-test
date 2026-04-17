import asyncio
import json
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

from agents.agent_store import agent_store
from agents.main_agent import MainAgent
from api.websocket.event_bus import event_bus
from config.time_utils import get_utc_timestamp
from database.task_store import task_store

logger = logging.getLogger(__name__)

MAX_WAVES = 20
_CRITERIA_KEY = "acceptance_criteria"
_CHECKLIST_KEY = "maestro_checklist"


class MaestroAgent:

    def __init__(self):
        self.task_store = task_store
        self.agent_store = agent_store
        import os
        self._max_workers = int(os.getenv("MAX_PARALLEL_WORKERS", "2"))

    def run_background(self, task_id: str) -> None:
        def _run():
            try:
                asyncio.run(self._run_task(task_id))
            finally:
                from database.task_store import close_connection
                close_connection()

        thread = threading.Thread(target=_run, daemon=True)
        thread.start()

    async def _run_task(self, task_id: str) -> None:
        try:
            self.task_store.update_task_status(task_id, "planning")
            task = self.task_store.get_task(task_id)
            event_bus.publish_sync({"type": "task_status", "task_id": task_id, "task": task})

            wave = 0
            first = True

            while True:
                if wave >= MAX_WAVES:
                    raise RuntimeError(f"Task {task_id} exceeded maximum wave limit ({MAX_WAVES})")

                spawned = await self._plan(task_id)

                if first:
                    first = False
                    self.task_store.update_task_status(task_id, "in_progress")
                    task = self.task_store.get_task(task_id)
                    event_bus.publish_sync({"type": "task_status", "task_id": task_id, "task": task})

                if not spawned:
                    break

                wave += 1
                await self._execute_wave(task_id)

            await self._compile(task_id)

            self.task_store.update_task_status(task_id, "completed")
            event_bus.publish_sync({"type": "task_completed", "task_id": task_id})

        except Exception as e:
            logger.error(f"Task {task_id} failed: {e}")
            self.task_store.update_task_status(task_id, "failed")
            self.task_store.log_event(task_id, "task_failed", f"Maestro error: {e}")
            event_bus.publish_sync({"type": "task_failed", "task_id": task_id, "error": str(e)})
            raise

    # ── Planning ───────────────────────────────────────────────────────────────

    async def _plan(self, task_id: str) -> bool:
        """Run one maestro planning turn. Returns True if new subtasks were spawned."""
        has_criteria = self.task_store.get_context(task_id, _CRITERIA_KEY) is not None
        count_before = len(self.task_store.get_subtasks_for_task(task_id))

        prompt = self._build_maestro_prompt(task_id, has_criteria)
        agent_data = self.agent_store.get("maestro")
        task = self.task_store.get_task(task_id)

        from tools.execution_context import execution_context
        maestro = MainAgent(agent_id="maestro", system_prompt=agent_data["system_prompt"])
        with execution_context(task_id=task_id, subtask_id="maestro", working_directory=task.get("working_directory")):
            response = maestro.chat(prompt) or ""

        self.task_store.write_context(
            task_id,
            key=f"maestro_planning_{datetime.now().strftime('%H%M%S')}",
            value=response,
        )
        self.task_store.log_event(task_id, "agent_message", f"Maestro (planning): {response[:200]}")

        count_after = len(self.task_store.get_subtasks_for_task(task_id))
        spawned = count_after > count_before
        if not spawned:
            logger.info(f"Task {task_id}: maestro spawned no subtasks — proceeding to compile")
        return spawned

    def _build_maestro_prompt(self, task_id: str, has_criteria: bool) -> str:
        task = self.task_store.get_task(task_id)
        subtasks = self.task_store.get_subtasks_for_task(task_id)
        all_context = self.task_store.get_all_context(task_id)

        lines = [
            f"TASK ID: {task_id}",
            f"ORIGINAL GOAL: {task['goal']}",
            "",
        ]

        if has_criteria:
            criteria_raw = self.task_store.get_context(task_id, _CRITERIA_KEY)
            if criteria_raw:
                try:
                    criteria = json.loads(criteria_raw)
                    lines.append("ACCEPTANCE CRITERIA (committed at start of task):")
                    for i, c in enumerate(criteria):
                        if isinstance(c, dict):
                            lines.append(f"  [{c.get('id', i)}] {c.get('criterion', c.get('text', str(c)))}")
                        else:
                            lines.append(f"  [{i}] {c}")
                    lines.append("")
                except Exception:
                    pass

            checklist_raw = self.task_store.get_context(task_id, _CHECKLIST_KEY)
            if checklist_raw:
                try:
                    checklist = json.loads(checklist_raw)
                    lines.append("PLANNING CHECKLIST (last update):")
                    for item in checklist:
                        notes = f" — {item['notes']}" if item.get("notes") else ""
                        lines.append(
                            f"  [{item['status'].upper()}] {item['id']} | {item['item']} "
                            f"(→ {item['linked_criteria_id']}){notes}"
                        )
                    lines.append("")
                except Exception:
                    pass

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

                is_domain = s["agent_id"] == "maestro"
                agent_label = "DOMAIN MAESTRO" if is_domain else s["agent_id"]
                lines.append(
                    f"  [{s['status'].upper()}] {s['id'][:8]} |{name_desc} "
                    f"agent={agent_label} | pos={s['position']}{dep_str}"
                )
                if is_domain and isinstance(s.get("input_context"), dict):
                    child_task_id = s["input_context"].get("child_task_id")
                    if child_task_id:
                        try:
                            child_task = self.task_store.get_task(child_task_id)
                            child_subtasks = self.task_store.get_subtasks_for_task(child_task_id)
                            done = sum(1 for cs in child_subtasks if cs["status"] == "completed")
                            lines.append(f"    Child task status: {child_task['status']} ({done}/{len(child_subtasks)} subtasks done)")
                        except Exception:
                            pass
                elif s.get("output"):
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

        if not has_criteria:
            lines.append(
                "INSTRUCTION: This is the initial planning phase. Before spawning any subtasks, "
                "call set_acceptance_criteria with a list of concrete, verifiable things that must "
                "all be true for this goal to be complete. Make reasonable assumptions — commit to "
                "a clear definition of done. Once criteria are set, spawn the first wave of subtasks."
            )
        else:
            lines.append(
                "INSTRUCTION: This is a re-planning pass. The acceptance criteria above are fixed — "
                "do not change them. Call read_plan to re-orient yourself, then call update_checklist "
                "to reflect current state (mark completed items done, add any newly discovered items). "
                "Then spawn the subtasks needed to satisfy any remaining criteria. "
                "Only stop spawning when every acceptance criterion will be met by existing or "
                "in-progress work."
            )

        return "\n".join(lines)

    # ── Execution ──────────────────────────────────────────────────────────────

    async def _execute_wave(self, task_id: str) -> None:
        """Execute all ready subtasks in parallel and wait for completion."""
        ready = self.task_store.get_ready_subtasks(task_id)
        if not ready:
            logger.info(f"Task {task_id}: no ready subtasks")
            return

        loop = asyncio.get_running_loop()
        with ThreadPoolExecutor(max_workers=self._max_workers) as executor:
            aws = [loop.run_in_executor(executor, self._execute_subtask, s, task_id) for s in ready]
            results = await asyncio.gather(*aws, return_exceptions=True)
            for subtask, result in zip(ready, results):
                if isinstance(result, Exception):
                    logger.error(f"Subtask {subtask['id']} failed: {result}")

    def _execute_subtask(self, subtask: Dict, task_id: str) -> str:
        subtask_id = subtask["id"]
        try:
            self.task_store.update_subtask_status(subtask_id, "in_progress")
            event_bus.publish_sync({
                "type": "subtask_started",
                "task_id": task_id,
                "subtask_id": subtask_id,
                "agent_id": subtask["agent_id"],
                "timestamp": get_utc_timestamp(),
            })

            output = (
                self._execute_domain(subtask, task_id)
                if subtask["agent_id"] == "maestro"
                else self._execute_worker(subtask, task_id)
            )

            self.task_store.set_subtask_output(subtask_id, output)
            event_bus.publish_sync({
                "type": "subtask_completed",
                "task_id": task_id,
                "subtask_id": subtask_id,
                "agent_id": subtask["agent_id"],
                "timestamp": get_utc_timestamp(),
            })
            return output

        except Exception as e:
            import traceback
            error_details = f"Subtask {subtask_id} failed: {e}\nTraceback:\n{traceback.format_exc()}"
            logger.error(error_details)
            self.task_store.update_subtask_status(subtask_id, "failed")
            self.task_store.log_event(task_id, "subtask_failed", error_details, subtask_id)
            event_bus.publish_sync({
                "type": "subtask_failed",
                "task_id": task_id,
                "subtask_id": subtask_id,
                "error": str(e),
                "timestamp": get_utc_timestamp(),
            })
            raise

    def _execute_domain(self, subtask: Dict, task_id: str) -> str:
        """Run a child maestro loop for a domain subtask (called from a thread)."""
        fresh = self.task_store.get_subtask(subtask["id"])
        input_ctx = fresh.get("input_context") or {}
        child_task_id = input_ctx.get("child_task_id")
        if not child_task_id:
            raise ValueError(f"Domain subtask {subtask['id']} missing child_task_id")

        logger.info(f"Executing domain subtask {subtask['id']} → child task {child_task_id}")
        asyncio.run(self._run_task(child_task_id))

        return (
            self.task_store.get_context(child_task_id, "final_output")
            or f"Domain '{subtask.get('name', child_task_id)}' completed."
        )

    def _execute_worker(self, subtask: Dict, task_id: str) -> str:
        """Run a worker agent for a subtask (called from a thread)."""
        task = self.task_store.get_task(task_id)
        fresh = self.task_store.get_subtask(subtask["id"])
        context_text = self._build_subtask_context(task_id, fresh)

        agent_id = fresh["agent_id"]
        if not self.agent_store.exists(agent_id):
            logger.warning(f"Agent '{agent_id}' not found, using 'worker'")
            agent_id = "worker"

        from tools.execution_context import execution_context
        agent = MainAgent(agent_id=agent_id)
        with execution_context(task_id=task_id, subtask_id=fresh["id"], working_directory=task.get("working_directory")):
            return agent.chat(f"{context_text}\n\nTask: {fresh['goal']}")

    def _build_subtask_context(self, task_id: str, subtask: Dict) -> str:
        task = self.task_store.get_task(task_id)
        lines = [f"Overall task goal: {task['goal']}", ""]

        input_context = subtask.get("input_context")
        context_keys = input_context.get("context_keys", []) if isinstance(input_context, dict) else []

        for key in context_keys:
            value = self.task_store.get_context(task_id, key)
            if value:
                lines.append(f"Context key '{key}': {value}")
        if context_keys:
            lines.append("")

        depends_on = subtask.get("depends_on", [])
        if depends_on:
            lines.append("Outputs from dependencies:")
            for dep_id in depends_on:
                try:
                    dep = self.task_store.get_subtask(dep_id)
                    if dep and dep.get("output"):
                        lines.append(f"\nOutput from {dep.get('agent_id', 'unknown')}:")
                        lines.append(dep["output"])
                except Exception as e:
                    logger.warning(f"Could not fetch dependency {dep_id}: {e}")
            lines.append("")

        return "\n".join(lines)

    # ── Compilation ────────────────────────────────────────────────────────────

    async def _compile(self, task_id: str) -> None:
        """Run summarizer and write final manifest."""
        event_bus.publish_sync({
            "type": "agent_message",
            "task_id": task_id,
            "agent_id": "summarizer",
            "message": "Summarizing results",
            "timestamp": get_utc_timestamp(),
        })

        if not self.agent_store.exists("summarizer"):
            logger.warning("No 'summarizer' agent — using fallback")
            self._fallback_compile(task_id)
            return

        task = self.task_store.get_task(task_id)
        subtasks = self.task_store.get_subtasks_for_task(task_id)
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

        from tools.execution_context import execution_context
        with execution_context(task_id=task_id, subtask_id="summarizer", working_directory=task.get("working_directory")):
            output = MainAgent(agent_id="summarizer").chat(prompt) or ""

        try:
            manifest = json.loads(output)
            summary = manifest.get("summary", "")
        except (json.JSONDecodeError, AttributeError):
            summary = output
            manifest = {"summary": summary, "artifacts": []}

        self.task_store.write_context(task_id, "final_output", summary)
        self.task_store.write_context(task_id, "final_manifest", json.dumps(manifest))
        self.task_store.log_event(task_id, "task_completed", "Summarization complete")

    def _fallback_compile(self, task_id: str) -> None:
        subtasks = self.task_store.get_subtasks_for_task(task_id)
        completed = [s for s in subtasks if s["status"] == "completed"]
        summary = f"Completed {len(completed)} subtask(s)."

        artifacts = []
        task_output_dir = Path(f"outputs/{task_id}")
        if task_output_dir.exists():
            for file_path in task_output_dir.rglob("*"):
                if file_path.is_file():
                    rel = file_path.relative_to("outputs")
                    artifacts.append({
                        "type": "zip" if file_path.suffix in (".zip", ".tar", ".gz") else "file",
                        "label": file_path.name,
                        "path": f"outputs/{rel}",
                    })

        manifest = {"summary": summary, "artifacts": artifacts}
        self.task_store.write_context(task_id, "final_output", summary)
        self.task_store.write_context(task_id, "final_manifest", json.dumps(manifest))
