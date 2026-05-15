import sys as _sys, pathlib as _pl
_src = _pl.Path(__file__).resolve().parent.parent / "src"
if str(_src) not in _sys.path:
    _sys.path.insert(0, str(_src))

import json
import shutil
import tempfile
from pathlib import Path

from eval._paths import FIXTURES_DIR


def capture(pipeline_name: str, brief: dict, brief_name: str) -> Path:
    """
    Run the pipeline once and save every intermediate JSON output as a fixture.
    These fixtures let stage runners reconstruct the inputs a stage would normally
    receive without re-running all prior stages.
    """
    from pipelines.registry import run_pipeline

    out_dir = FIXTURES_DIR / pipeline_name / brief_name
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"Running {pipeline_name} pipeline to capture fixtures...")
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        run_pipeline(pipeline_name, brief, tmp)

        captured = 0
        for json_file in sorted(tmp_path.glob("*.json")):
            dest = out_dir / json_file.name
            shutil.copy2(json_file, dest)
            print(f"  captured: {json_file.name}")
            captured += 1

    print(f"Done — {captured} fixtures saved to {out_dir}")
    return out_dir


def load(pipeline_name: str, brief_name: str) -> dict:
    """Load all captured fixtures for a pipeline/brief pair."""
    fixture_dir = FIXTURES_DIR / pipeline_name / brief_name
    if not fixture_dir.exists():
        raise FileNotFoundError(
            f"No fixtures at {fixture_dir}\n"
            f"Run first:  python eval/cli.py capture {pipeline_name} --brief {brief_name}"
        )
    return {
        p.name: json.loads(p.read_text(encoding="utf-8"))
        for p in sorted(fixture_dir.glob("*.json"))
    }
