"""Pipeline execution tools — registered into the tool manager."""

import json
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict

from tools.tool_manager import tool_manager
from tools.execution_context import execution_context

# Per-session queue: task_id → list of (pipeline_name, brief, working_dir)
_queue: Dict[str, list] = {}
_queue_lock = threading.Lock()


def _get_working_dir(pipeline_name: str) -> str:
    base = execution_context.working_directory or "outputs"
    return str(Path(base) / "pipelines" / pipeline_name)


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

    task_id = execution_context.task_id or "default"
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

    task_id = execution_context.task_id or "default"
    with _queue_lock:
        items = _queue.pop(task_id, [])

    if not items:
        return json.dumps({"status": "error", "error": "No pipelines queued."})

    results = {}
    errors = {}

    with ThreadPoolExecutor(max_workers=len(items)) as executor:
        futures = {
            executor.submit(_run, name, brief, working_dir): name
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


@tool_manager.tool(
    description="List all available pipelines and the brief parameters they require.",
)
def list_pipelines() -> str:
    from pipelines.registry import get_registry
    registry = get_registry()
    summary = {
        name: {"description": defn.description, "brief_schema": defn.brief_schema}
        for name, defn in registry.items()
    }
    return json.dumps(summary, indent=2)
