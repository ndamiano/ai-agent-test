import sys as _sys, pathlib as _pl
_src = _pl.Path(__file__).resolve().parent.parent / "src"
if str(_src) not in _sys.path:
    _sys.path.insert(0, str(_src))

import io
import json
import logging
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as _FuturesTimeout
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class _Tee:
    """Write to both original stdout and a capture buffer simultaneously."""
    def __init__(self):
        self._orig = sys.stdout
        self._buf = io.StringIO()

    def write(self, s: str) -> int:
        self._orig.write(s)
        self._buf.write(s)
        return len(s)

    def flush(self):
        self._orig.flush()

    def getvalue(self) -> str:
        return self._buf.getvalue()


def _get_pipeline_stage(pipeline_name: str, stage_id: str):
    from pipelines.registry import get_registry
    registry = get_registry()
    if pipeline_name not in registry:
        raise ValueError(f"Unknown pipeline: {pipeline_name!r}")
    pipeline = registry[pipeline_name].pipeline
    for node in pipeline.nodes:
        for stage in node.stages:
            if stage.id == stage_id:
                return pipeline, node, stage
    raise ValueError(f"Stage {stage_id!r} not found in pipeline {pipeline_name!r}")


class StageRunner:
    """
    Runs a single pipeline stage in isolation, repeatedly within a time budget.

    Fixtures (the JSON files prior stages would have written) must be supplied
    up front so the stage can read its inputs without running the full pipeline.
    """

    def __init__(self, pipeline_name: str, stage_id: str, fixtures: dict):
        self.pipeline_name = pipeline_name
        self.stage_id = stage_id
        self.fixtures = fixtures  # {filename: data}
        self._pipeline, self._node, self._stage = _get_pipeline_stage(pipeline_name, stage_id)

    def _run_once(self, tmp_dir: Path) -> dict:
        from pipelines.runner import PipelineRunner

        for fname, data in self.fixtures.items():
            (tmp_dir / fname).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

        runner = PipelineRunner(str(tmp_dir))
        runner.task_id = None  # suppress WebSocket events

        tee = _Tee()
        t0 = time.monotonic()
        sys.stdout = tee
        try:
            ok = runner._run_stage(self._pipeline, self._stage, self._node.id)
        finally:
            sys.stdout = tee._orig
        elapsed = time.monotonic() - t0

        output = None
        if ok:
            out_path = tmp_dir / self._stage.output
            if out_path.exists():
                output = json.loads(out_path.read_text(encoding="utf-8"))

        return {"ok": ok, "output": output, "elapsed": elapsed, "log": tee.getvalue()}

    def run_timed(
        self,
        time_budget: float,
        max_n: Optional[int] = None,
        per_run_timeout: Optional[float] = None,
    ) -> list:
        results = []
        deadline = time.monotonic() + time_budget
        i = 0

        while time.monotonic() < deadline and (max_n is None or i < max_n):
            with tempfile.TemporaryDirectory() as tmp:
                tmp_path = Path(tmp)
                t0 = time.monotonic()
                if per_run_timeout is not None:
                    with ThreadPoolExecutor(max_workers=1) as ex:
                        future = ex.submit(self._run_once, tmp_path)
                        try:
                            result = future.result(timeout=per_run_timeout)
                        except _FuturesTimeout:
                            result = {"ok": False, "output": None, "log": "",
                                      "elapsed": time.monotonic() - t0, "error": "timeout"}
                else:
                    result = self._run_once(tmp_path)

            results.append(result)
            i += 1
            remaining = max(0.0, deadline - time.monotonic())
            tag = f"TIMEOUT" if result.get("error") == "timeout" else ("ok  " if result["ok"] else "FAIL")
            print(f"  run {i:3d}  {tag}  {result['elapsed']:.1f}s  ({remaining:.0f}s remaining)")

        return results


class PipelineEvalRunner:
    """Runs a full pipeline end-to-end repeatedly within a time budget."""

    def __init__(self, pipeline_name: str, brief: dict):
        self.pipeline_name = pipeline_name
        self.brief = brief

    def run_timed(
        self,
        time_budget: float,
        max_n: Optional[int] = None,
        per_run_timeout: Optional[float] = None,
    ) -> list:
        from pipelines.registry import run_pipeline

        def _run(tmp: str) -> dict:
            t0 = time.monotonic()
            try:
                outputs = run_pipeline(self.pipeline_name, self.brief, tmp)
                return {"ok": True, "outputs": outputs, "elapsed": time.monotonic() - t0}
            except Exception as e:
                return {"ok": False, "error": str(e), "elapsed": time.monotonic() - t0}

        results = []
        deadline = time.monotonic() + time_budget
        i = 0

        while time.monotonic() < deadline and (max_n is None or i < max_n):
            with tempfile.TemporaryDirectory() as tmp:
                t0 = time.monotonic()
                if per_run_timeout is not None:
                    with ThreadPoolExecutor(max_workers=1) as ex:
                        future = ex.submit(_run, tmp)
                        try:
                            result = future.result(timeout=per_run_timeout)
                        except _FuturesTimeout:
                            result = {"ok": False, "outputs": {},
                                      "elapsed": time.monotonic() - t0, "error": "timeout"}
                else:
                    result = _run(tmp)

            results.append(result)
            i += 1
            remaining = max(0.0, deadline - time.monotonic())
            tag = "TIMEOUT" if result.get("error") == "timeout" else ("ok  " if result["ok"] else "FAIL")
            print(f"  run {i:3d}  {tag}  {result['elapsed']:.1f}s  ({remaining:.0f}s remaining)")

        return results
