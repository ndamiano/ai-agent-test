import json
import logging
import re
import contextvars
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Union

from llm_clients.inference import PipelineAgent, strip_fences

logger = logging.getLogger(__name__)


@dataclass
class LLMStage:
    id: str
    prompt_template: str
    output: str
    schema: Optional[Dict] = None
    retries: int = 2
    max_tokens: Optional[int] = None


@dataclass
class FnStage:
    id: str
    fn: Callable
    inputs: List[str]
    output: str
    retries: int = 2
    prompt_file: Optional[str] = None
    max_tokens: Optional[int] = None


Stage = Union[LLMStage, FnStage]


@dataclass
class Node:
    id: str
    stages: List[Stage]


@dataclass
class Pipeline:
    name: str
    nodes: List[Node]
    prompts_dir: Path


def render_template(template_path: Path, inputs: Dict) -> str:
    if not template_path.exists():
        raise FileNotFoundError(f"Prompt template not found: {template_path}")

    template_str = template_path.read_text(encoding="utf-8")

    def replace(match):
        key = match.group(1).strip()
        as_json = key.endswith("|json")
        if as_json:
            key = key[:-5].strip()
        if key not in inputs:
            logger.warning("render_template: missing key %r in template %s", key, template_path.name)
            return "" if not as_json else "null"
        value = inputs[key]
        if as_json or isinstance(value, (dict, list)):
            return json.dumps(value, indent=2)
        return str(value)

    return re.sub(r"\{([A-Za-z_][A-Za-z0-9_|]*)\}", replace, template_str)


def _enrich_inputs(inputs: dict) -> dict:
    """Add slim reference lists so prompts can reference id/name/role without full objects."""
    extra = {}
    chars = inputs.get("characters", [])
    if isinstance(chars, dict):
        chars = chars.get("characters", [])
    if chars:
        extra["character_refs"] = [
            {"id": c.get("id"), "name": c.get("name"), "role": c.get("role"),
             "description": c.get("description", ""), "personality": c.get("personality", [])}
            for c in chars
        ]

    settings = inputs.get("settings", [])
    if isinstance(settings, dict):
        settings = settings.get("settings", [])
    if settings:
        extra["setting_refs"] = [{"id": s.get("id"), "name": s.get("name")} for s in settings]

    return {**inputs, **extra}


class PipelineRunner:

    def __init__(self, working_dir: str):
        from tools.execution_context import get_pipeline_path, get_subtask_id, get_task_id

        self.working_dir = Path(working_dir)
        self.working_dir.mkdir(parents=True, exist_ok=True)
        self.task_id = get_task_id()
        self.subtask_id = get_subtask_id()
        self.parent_pipeline_path = get_pipeline_path()
        self.pipeline_path: List[str] = []

    def _publish_progress(
        self,
        event_type: str,
        pipeline_name: str,
        **payload: Any,
    ) -> None:
        if not self.task_id:
            return

        from api.websocket.event_bus import event_bus
        from config.time_utils import get_utc_timestamp

        event = {
            "type": event_type,
            "task_id": self.task_id,
            "subtask_id": self.subtask_id,
            "pipeline": pipeline_name,
            "pipeline_path": self.pipeline_path or [pipeline_name],
            "parent_pipeline": self.parent_pipeline_path[-1] if self.parent_pipeline_path else None,
            "working_dir": str(self.working_dir),
            "timestamp": get_utc_timestamp(),
            **payload,
        }
        event_bus.publish_sync(event)

    def run(self, pipeline: Pipeline, brief: Dict[str, Any]) -> bool:
        from tools.execution_context import pipeline_context
        from llm_clients.log_context import set_log_dir

        self.pipeline_path = [*self.parent_pipeline_path, pipeline.name]

        import shutil
        if self.working_dir.exists():
            shutil.rmtree(self.working_dir)
        self.working_dir.mkdir(parents=True, exist_ok=True)

        log_dir = self.working_dir / "llm_logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        token = set_log_dir(str(log_dir))
        try:
            with pipeline_context(self.pipeline_path):
                self._write("brief.json", brief)

                print(f"\n{'='*60}")
                print(f"Pipeline: {pipeline.name}  ({len(pipeline.nodes)} nodes)")
                print(f"Working dir: {self.working_dir.resolve()}")
                print(f"{'='*60}\n")

                self._publish_progress(
                    "pipeline_started",
                    pipeline.name,
                    node_count=len(pipeline.nodes),
                )

                for node in pipeline.nodes:
                    if not self._run_node(pipeline, node):
                        print(f"\n  Pipeline failed at node: [{node.id}]")
                        self._publish_progress(
                            "pipeline_failed",
                            pipeline.name,
                            node_id=node.id,
                            error=f"Pipeline failed at node: {node.id}",
                        )
                        return False

                print("\n  Pipeline complete")
                self._publish_progress("pipeline_completed", pipeline.name)
                return True
        finally:
            from llm_clients.log_context import reset_log_dir
            reset_log_dir(token)

    def run_from(self, pipeline: Pipeline, brief: Dict[str, Any], node_id: str) -> bool:
        start = next((i for i, n in enumerate(pipeline.nodes) if n.id == node_id), None)
        if start is None:
            raise ValueError(f"Node '{node_id}' not found in pipeline '{pipeline.name}'")
        tail = Pipeline(name=pipeline.name, nodes=pipeline.nodes[start:], prompts_dir=pipeline.prompts_dir)
        return self.run(tail, brief)

    def _run_node(self, pipeline: Pipeline, node: Node) -> bool:
        n = len(node.stages)
        print(f"  Node [{node.id}]  ({n} stage{'s' if n > 1 else ''})")
        self._publish_progress(
            "pipeline_node_started",
            pipeline.name,
            node_id=node.id,
            stage_count=n,
        )

        if n == 1:
            ok = self._run_stage(pipeline, node.stages[0], node.id)
            self._publish_progress(
                "pipeline_node_completed" if ok else "pipeline_node_failed",
                pipeline.name,
                node_id=node.id,
            )
            return ok

        results: Dict[str, bool] = {}
        with ThreadPoolExecutor(max_workers=n) as executor:
            futures = {
                executor.submit(contextvars.copy_context().run, self._run_stage, pipeline, s, node.id): s
                for s in node.stages
            }
            for future in as_completed(futures):
                stage = futures[future]
                try:
                    results[stage.id] = future.result()
                except Exception:
                    logger.exception(f"Stage {stage.id} raised an unhandled exception")
                    results[stage.id] = False

        ok = all(results.values())
        self._publish_progress(
            "pipeline_node_completed" if ok else "pipeline_node_failed",
            pipeline.name,
            node_id=node.id,
        )
        return ok

    def _run_stage(self, pipeline: Pipeline, stage: Stage, node_id: str) -> bool:
        label = f"[{stage.id}]"

        for attempt in range(1, stage.retries + 2):
            self._publish_progress(
                "pipeline_stage_started",
                pipeline.name,
                node_id=node_id,
                stage_id=stage.id,
                stage_type="llm" if isinstance(stage, LLMStage) else "function",
                attempt=attempt,
                max_attempts=stage.retries + 1,
            )
            try:
                if isinstance(stage, LLMStage):
                    output_data = self._run_llm_stage(pipeline, stage)
                elif isinstance(stage, FnStage):
                    output_data = self._run_fn_stage(stage)
                else:
                    raise TypeError(f"Unknown stage type: {type(stage)}")

                if self._validate(label, stage, output_data):
                    self._write(stage.output, output_data)
                    print(f"    {label}  wrote {stage.output}")
                    self._publish_progress(
                        "pipeline_stage_completed",
                        pipeline.name,
                        node_id=node_id,
                        stage_id=stage.id,
                        output=stage.output,
                        attempt=attempt,
                    )
                    return True

                print(f"    {label}  validation failed (attempt {attempt})")
                if attempt <= stage.retries:
                    self._publish_progress(
                        "pipeline_stage_retrying",
                        pipeline.name,
                        node_id=node_id,
                        stage_id=stage.id,
                        attempt=attempt,
                        error="Validation failed",
                    )

            except Exception as e:
                print(f"    {label}  error on attempt {attempt}: {e}")
                logger.exception(f"Stage {stage.id} attempt {attempt}")
                if attempt <= stage.retries:
                    self._publish_progress(
                        "pipeline_stage_retrying",
                        pipeline.name,
                        node_id=node_id,
                        stage_id=stage.id,
                        attempt=attempt,
                        error=str(e),
                    )

            if attempt <= stage.retries:
                print(f"    {label}  retrying...")

        self._publish_progress(
            "pipeline_stage_failed",
            pipeline.name,
            node_id=node_id,
            stage_id=stage.id,
        )
        return False

    def _run_llm_stage(self, pipeline: Pipeline, stage: LLMStage) -> Dict:
        inputs = _enrich_inputs(self._load_all())
        prompt = render_template(pipeline.prompts_dir / stage.prompt_template, inputs)

        agent = PipelineAgent(max_tokens=stage.max_tokens) if stage.max_tokens else PipelineAgent()
        content = strip_fences(agent.send(prompt))

        # One correction attempt before letting the outer retry loop handle it
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            content = strip_fences(agent.send(
                "Invalid JSON. Return only the JSON object, no other text."
            ))
            try:
                return json.loads(content)
            except json.JSONDecodeError as e:
                raise RuntimeError(f"LLM returned invalid JSON: {e}\n\nContent:\n{content}")

    def _run_fn_stage(self, stage: FnStage) -> Any:
        missing = [f for f in stage.inputs if not (self.working_dir / f).exists()]
        if missing:
            raise FileNotFoundError(f"Missing inputs for [{stage.id}]: {missing}")
        kwargs = {}
        if stage.max_tokens is not None:
            kwargs["max_tokens"] = stage.max_tokens
        return stage.fn(self._load(stage.inputs), self.working_dir, **kwargs)

    def _validate(self, label: str, stage: Stage, data: Any) -> bool:
        if not isinstance(data, dict):
            print(f"    {label}  output is not a dict: {type(data)}")
            return False
        schema = getattr(stage, "schema", None)
        if schema:
            missing = [k for k in schema.get("required", []) if k not in data]
            if missing:
                print(f"    {label}  missing required keys: {missing}")
                return False
        return True

    def _load_all(self) -> Dict:
        merged: Dict = {}
        for path in sorted(self.working_dir.glob("*.json")):
            stem = path.stem
            data = json.loads(path.read_text(encoding="utf-8"))
            merged[stem] = data
            if isinstance(data, dict):
                # Don't overwrite the stem key — file named foo.json with top-level key "foo"
                # should stay accessible as inputs["foo"] = the full dict, not the inner value.
                for k, v in data.items():
                    if k != stem:
                        merged[k] = v
        return merged

    def _load(self, filenames: List[str]) -> Dict:
        merged: Dict = {}
        for filename in filenames:
            stem = Path(filename).stem
            path = self.working_dir / filename
            data = json.loads(path.read_text(encoding="utf-8"))
            merged[stem] = data
            if isinstance(data, dict):
                for k, v in data.items():
                    if k != stem:
                        merged[k] = v
        return merged

    def _write(self, filename: str, data: Any):
        from tools.execution_context import track_written_file
        path = self.working_dir / filename
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        track_written_file(str(path))
