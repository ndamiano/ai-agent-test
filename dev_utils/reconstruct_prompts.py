#!/usr/bin/env python3
"""
Reconstruct the exact prompts used in a pipeline run using the actual pipeline
prompt-building functions. No duplication — if the pipeline changes, this
automatically reflects those changes.

Usage:
    python dev_utils/reconstruct_prompts.py <run_dir>

Output:
    <run_dir>/prompts/01_bible.txt
    <run_dir>/prompts/02_scene_plan.txt
    ...

Supports: renpy, renpy_vn_graph pipelines.
Pipeline is auto-detected from the run directory contents.
"""
import argparse
import json
import sys
from pathlib import Path

REPO_DIR = Path(__file__).resolve().parent.parent
SRC_DIR  = REPO_DIR / "src"
for p in [str(REPO_DIR), str(SRC_DIR)]:
    if p not in sys.path:
        sys.path.insert(0, p)


def _write(path: Path, content: str):
    path.write_text(content, encoding="utf-8")
    print(f"  wrote {path.name}")


def reconstruct_renpy(run_dir: Path, out_dir: Path):
    from pipelines.renpy.fns import (
        _build_bible_prompt,
        _build_scene_plan_prompt,
        _build_asset_manifest_prompt,
        _build_scene_script_prompts,
    )
    brief      = json.loads((run_dir / "brief.json").read_text())
    bible      = json.loads((run_dir / "bible.json").read_text())
    scene_plan = json.loads((run_dir / "scene_plan.json").read_text())
    manifest   = json.loads((run_dir / "asset_manifest.json").read_text())

    _write(out_dir / "01_bible.txt", _build_bible_prompt(brief))
    _write(out_dir / "02_scene_plan.txt", _build_scene_plan_prompt(brief, bible))
    _write(out_dir / "03_asset_manifest.txt", _build_asset_manifest_prompt(brief, bible, scene_plan))

    for i, (sid, system, user) in enumerate(_build_scene_script_prompts(bible, scene_plan, manifest)):
        fname = f"04_scene_{i+1:02d}_{sid}.txt"
        _write(out_dir / fname, f"[SYSTEM]\n{system}\n\n[USER]\n{user}")


def reconstruct_vn_graph(run_dir: Path, out_dir: Path):
    from pipelines.renpy_vn_graph.fns import _build_node_script_prompts

    premise  = json.loads((run_dir / "premise.json").read_text())
    dag      = json.loads((run_dir / "graph.json").read_text())
    beat_map_raw = json.loads((run_dir / "beat_map.json").read_text())
    beat_map = beat_map_raw.get("beat_map", beat_map_raw)

    for i, (nid, system, user) in enumerate(_build_node_script_prompts(premise, dag, beat_map)):
        node_type = dag.get("nodes", {}).get(nid, {}).get("type", "")
        fname = f"{i+1:02d}_node_{nid}_{node_type}.txt"
        _write(out_dir / fname, f"[SYSTEM]\n{system}\n\n[USER]\n{user}")


def _detect_pipeline(run_dir: Path) -> str:
    if (run_dir / "premise.json").exists() and (run_dir / "beat_map.json").exists():
        return "renpy_vn_graph"
    if (run_dir / "bible.json").exists() and (run_dir / "scene_plan.json").exists():
        return "renpy"
    return "unknown"


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("run_dir", help="Path to a pipeline run directory")
    args = parser.parse_args()

    run_dir = Path(args.run_dir).resolve()
    if not run_dir.exists():
        sys.exit(f"Not found: {run_dir}")

    pipeline = _detect_pipeline(run_dir)
    out_dir  = run_dir / "prompts"
    out_dir.mkdir(exist_ok=True)
    print(f"Pipeline: {pipeline}")
    print(f"Reconstructing prompts for: {run_dir.name}")
    print(f"Output: {out_dir}\n")

    if pipeline == "renpy":
        reconstruct_renpy(run_dir, out_dir)
    elif pipeline == "renpy_vn_graph":
        reconstruct_vn_graph(run_dir, out_dir)
    else:
        sys.exit(f"Unknown pipeline — missing expected files in {run_dir}")

    print("\nDone.")


if __name__ == "__main__":
    main()
