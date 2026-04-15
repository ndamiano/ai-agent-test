import json
import logging
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Union

logger = logging.getLogger(__name__)


@dataclass
class LLMStage:
    id: str
    prompt_template: str
    output: str
    schema: Optional[Dict] = None
    retries: int = 2


@dataclass
class FnStage:
    id: str
    fn: Callable
    inputs: List[str]
    output: str
    retries: int = 2


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
    """
    Substitute {variable} and {variable|json} tokens in a prompt template.
    Missing keys render as [MISSING:key].
    """
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


def strip_fences(content: str) -> str:
    """Strip markdown code fences that models occasionally emit despite instructions."""
    if content.startswith("```"):
        content = content.split("```")[1]
        if content.startswith("json"):
            content = content[4:]
    return content.strip()


class PipelineRunner:

    def __init__(self, working_dir: str, connector=None):
        self.working_dir = Path(working_dir)
        self.working_dir.mkdir(parents=True, exist_ok=True)
        self._connector = connector

    def run(self, pipeline: Pipeline, brief: Dict[str, Any]) -> bool:
        self._write("brief.json", brief)

        print(f"\n{'='*60}")
        print(f"Pipeline: {pipeline.name}  ({len(pipeline.nodes)} nodes)")
        print(f"Working dir: {self.working_dir.resolve()}")
        print(f"{'='*60}\n")

        for node in pipeline.nodes:
            if not self._run_node(pipeline, node):
                print(f"\n  Pipeline failed at node: [{node.id}]")
                return False

        print("\n  Pipeline complete")
        return True

    def run_from(self, pipeline: Pipeline, brief: Dict[str, Any], node_id: str) -> bool:
        start = next((i for i, n in enumerate(pipeline.nodes) if n.id == node_id), None)
        if start is None:
            raise ValueError(f"Node '{node_id}' not found in pipeline '{pipeline.name}'")
        tail = Pipeline(name=pipeline.name, nodes=pipeline.nodes[start:], prompts_dir=pipeline.prompts_dir)
        return self.run(tail, brief)

    def _run_node(self, pipeline: Pipeline, node: Node) -> bool:
        n = len(node.stages)
        print(f"  Node [{node.id}]  ({n} stage{'s' if n > 1 else ''})")

        if n == 1:
            return self._run_stage(pipeline, node.stages[0])

        results: Dict[str, bool] = {}
        with ThreadPoolExecutor(max_workers=n) as executor:
            futures = {executor.submit(self._run_stage, pipeline, s): s for s in node.stages}
            for future in as_completed(futures):
                stage = futures[future]
                try:
                    results[stage.id] = future.result()
                except Exception:
                    logger.exception(f"Stage {stage.id} raised an unhandled exception")
                    results[stage.id] = False

        return all(results.values())

    def _run_stage(self, pipeline: Pipeline, stage: Stage) -> bool:
        label = f"[{stage.id}]"

        for attempt in range(1, stage.retries + 2):
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
                    return True

                print(f"    {label}  validation failed (attempt {attempt})")

            except Exception as e:
                print(f"    {label}  error on attempt {attempt}: {e}")
                logger.exception(f"Stage {stage.id} attempt {attempt}")

            if attempt <= stage.retries:
                print(f"    {label}  retrying...")

        return False

    def _run_llm_stage(self, pipeline: Pipeline, stage: LLMStage) -> Dict:
        inputs = self._load_all()
        prompt = render_template(pipeline.prompts_dir / stage.prompt_template, inputs)

        messages = [
            {
                "role": "system",
                "content": "You are a precise assistant. Output only valid JSON. "
                           "No markdown, no explanation, no code fences.",
            },
            {"role": "user", "content": prompt},
        ]

        result = self._get_connector().generate_with_tools(messages, [])
        if "error" in result:
            raise RuntimeError(f"LLM error: {result['error']}")

        content = strip_fences(result["choices"][0]["message"]["content"].strip())

        try:
            return json.loads(content)
        except json.JSONDecodeError as e:
            raise RuntimeError(f"LLM returned invalid JSON: {e}\n\nContent:\n{content}")

    def _run_fn_stage(self, stage: FnStage) -> Any:
        missing = [f for f in stage.inputs if not (self.working_dir / f).exists()]
        if missing:
            raise FileNotFoundError(f"Missing inputs for [{stage.id}]: {missing}")
        inputs = self._load(stage.inputs)
        return stage.fn(inputs, self.working_dir)

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
        """Load every JSON file in the working dir, merging top-level keys so templates can reference them directly."""
        merged: Dict = {}
        for path in sorted(self.working_dir.glob("*.json")):
            data = json.loads(path.read_text(encoding="utf-8"))
            merged[path.stem] = data
            if isinstance(data, dict):
                merged.update(data)
        return merged

    def _load(self, filenames: List[str]) -> Dict:
        merged: Dict = {}
        for filename in filenames:
            path = self.working_dir / filename
            data = json.loads(path.read_text(encoding="utf-8"))
            merged[Path(filename).stem] = data
            if isinstance(data, dict):
                merged.update(data)
        return merged

    def _write(self, filename: str, data: Any):
        path = self.working_dir / filename
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    def _get_connector(self):
        if self._connector is None:
            sys.path.insert(0, str(Path(__file__).parent.parent))
            from llm_clients.connector_selector import get_connector
            self._connector = get_connector()
        return self._connector
