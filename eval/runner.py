import sys as _sys
import pathlib as _pl
_src = _pl.Path(__file__).resolve().parent.parent / "src"
if str(_src) not in _sys.path:
    _sys.path.insert(0, str(_src))

import io
import json
import logging
import queue
import random
import shutil
import sys
import tempfile
import time
import uuid
from pathlib import Path

logger = logging.getLogger(__name__)

_WATCHED_LOGGERS = ["pipelines", "llm_clients"]


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


class _QueueHandler(logging.Handler):
    """Logging handler that accumulates records into a thread-safe queue."""
    def __init__(self, q: queue.Queue):
        super().__init__()
        self._q = q

    def emit(self, record: logging.LogRecord):
        try:
            self._q.put_nowait(self.format(record))
        except Exception:
            self.handleError(record)


def _drain(q: queue.Queue) -> str:
    lines = []
    while True:
        try:
            lines.append(q.get_nowait())
        except queue.Empty:
            break
    return "\n".join(lines)


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
    Runs a single pipeline stage in isolation, N times.

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

        log_q: queue.Queue = queue.Queue()
        handler = _QueueHandler(log_q)
        handler.setFormatter(logging.Formatter("%(name)s %(levelname)s: %(message)s"))
        watched = [logging.getLogger(n) for n in _WATCHED_LOGGERS]
        for lgr in watched:
            lgr.addHandler(handler)

        tee = _Tee()
        t0 = time.monotonic()
        sys.stdout = tee
        try:
            ok = runner._run_stage(self._pipeline, self._stage, self._node.id)
        finally:
            sys.stdout = tee._orig
            for lgr in watched:
                lgr.removeHandler(handler)

        elapsed = time.monotonic() - t0

        stdout_log = tee.getvalue()
        logger_log = _drain(log_q)
        combined = "\n".join(s for s in [stdout_log, logger_log] if s.strip())

        output = None
        if ok:
            out_path = tmp_dir / self._stage.output
            if out_path.exists():
                output = json.loads(out_path.read_text(encoding="utf-8"))

        return {"ok": ok, "output": output, "elapsed": elapsed, "log": combined}

    def run_n(self, n: int) -> list:
        results = []
        for i in range(n):
            with tempfile.TemporaryDirectory() as tmp:
                result = self._run_once(Path(tmp))
            results.append(result)
            tag = "ok  " if result["ok"] else "FAIL"
            print(f"  run {i+1:3d}/{n}  {tag}  {result['elapsed']:.1f}s")
        return results


class PipelineEvalRunner:
    """Runs a full pipeline end-to-end N times.

    briefs: list of brief dicts. If more than one, a random brief is picked per run.
    """

    def __init__(self, pipeline_name: str, briefs: list[dict]):
        self.pipeline_name = pipeline_name
        self.briefs = briefs if isinstance(briefs, list) else [briefs]

    def run_n(self, n: int) -> list:
        from pipelines.registry import run_pipeline

        results = []
        for i in range(n):
            brief = random.choice(self.briefs)
            with tempfile.TemporaryDirectory() as tmp:
                tmp_path = Path(tmp)
                t0 = time.monotonic()
                try:
                    outputs = run_pipeline(self.pipeline_name, brief, tmp)
                    elapsed = time.monotonic() - t0
                    game_dir = self._save_game(tmp_path, brief)
                    result = {"ok": True, "outputs": outputs, "elapsed": elapsed, "game_dir": game_dir}
                except Exception as e:
                    result = {"ok": False, "error": str(e), "elapsed": time.monotonic() - t0}
            results.append(result)
            tag = "ok  " if result["ok"] else "FAIL"
            brief_tag = brief.get("genre", "?")
            game_note = f"  → {result['game_dir']}" if result.get("game_dir") else ""
            print(f"  run {i+1:3d}/{n}  {tag}  {result['elapsed']:.1f}s  [{brief_tag}]{game_note}")
        return results

    def _save_game(self, tmp_path: Path, brief: dict) -> str | None:
        from eval._paths import GAMES_DIR

        game_src = tmp_path / "game_output"
        if not game_src.exists():
            return None
        genre = brief.get("genre", "unknown").replace(" ", "_")[:20]
        uid = uuid.uuid4().hex[:8]
        dest = GAMES_DIR / f"{self.pipeline_name}_{genre}_{uid}"
        GAMES_DIR.mkdir(parents=True, exist_ok=True)
        shutil.copytree(game_src, dest)
        return str(dest)
