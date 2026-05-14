"""Pipeline execution tools — registered into the tool manager."""

import json
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict

from tools.tool_manager import tool_manager
from tools.execution_context import get_pipeline_path, get_subtask_id, get_task_id, get_working_directory

# Per-session queue: task_id → list of (pipeline_name, brief, working_dir)
_queue: Dict[str, list] = {}
_queue_lock = threading.Lock()


def _get_working_dir(pipeline_name: str) -> str:
    from datetime import datetime
    base = get_working_directory() or "outputs"
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    return str(Path(base) / "pipelines" / pipeline_name / run_id)


@tool_manager.tool(
    description=(
        "Run a pipeline immediately and wait for it to finish. "
        "Use this when you need the result before continuing. "
        "For running multiple independent pipelines at once, use queue_pipeline + run_queued_pipelines instead."
    ),
    param_hints={
        "name": {"type": "string", "description": "Pipeline name (e.g. 'renpy'). Use list_pipelines to see available pipelines."},
        "brief": {"type": "object", "description": "Input parameters for the pipeline. Keys vary by pipeline — use list_pipelines to see the schema."},
    },
)
def run_pipeline(name: str, brief: dict) -> str:
    from pipelines.registry import run_pipeline as _run, get_registry
    working_dir = _get_working_dir(name)
    try:
        outputs = _run(name, brief, working_dir)
        summary = {k: "(generated)" for k in outputs}
        return json.dumps({"status": "completed", "working_dir": working_dir, "outputs": summary})
    except Exception as e:
        return json.dumps({"status": "failed", "error": str(e)})


@tool_manager.tool(
    description=(
        "Add a pipeline to the queue to run in parallel with other queued pipelines. "
        "Does not run immediately — call run_queued_pipelines to execute all queued pipelines at once."
    ),
    param_hints={
        "name": {"type": "string", "description": "Pipeline name (e.g. 'renpy')."},
        "brief": {"type": "object", "description": "Input parameters for the pipeline."},
    },
)
def queue_pipeline(name: str, brief: dict) -> str:
    from pipelines.registry import get_registry
    registry = get_registry()
    if name not in registry:
        return json.dumps({"status": "error", "error": f"Unknown pipeline {name!r}. Available: {list(registry.keys())}"})

    task_id = get_task_id() or "default"
    working_dir = _get_working_dir(name)

    with _queue_lock:
        if task_id not in _queue:
            _queue[task_id] = []
        _queue[task_id].append((name, brief, working_dir))

    return json.dumps({"status": "queued", "pipeline": name, "position": len(_queue[task_id])})


@tool_manager.tool(
    description=(
        "Run all queued pipelines in parallel and wait for all to finish. "
        "Returns results from every pipeline. Use after queue_pipeline calls."
    ),
)
def run_queued_pipelines() -> str:
    from pipelines.registry import run_pipeline as _run
    from tools.execution_context import execution_context, pipeline_context

    context_task_id = get_task_id()
    context_subtask_id = get_subtask_id()
    context_working_directory = get_working_directory()
    context_pipeline_path = get_pipeline_path()
    task_id = context_task_id or "default"
    with _queue_lock:
        items = _queue.pop(task_id, [])

    if not items:
        return json.dumps({"status": "error", "error": "No pipelines queued."})

    results = {}
    errors = {}

    def _run_item(name: str, brief: dict, working_dir: str):
        with execution_context(
            task_id=context_task_id,
            subtask_id=context_subtask_id,
            working_directory=context_working_directory,
        ):
            with pipeline_context(context_pipeline_path):
                return _run(name, brief, working_dir)

    with ThreadPoolExecutor(max_workers=len(items)) as executor:
        futures = {
            executor.submit(_run_item, name, brief, working_dir): name
            for name, brief, working_dir in items
        }
        for future in as_completed(futures):
            name = futures[future]
            try:
                outputs = future.result()
                results[name] = {"status": "completed", "outputs": {k: "(generated)" for k in outputs}}
            except Exception as e:
                errors[name] = str(e)

    return json.dumps({"completed": results, "failed": errors})
