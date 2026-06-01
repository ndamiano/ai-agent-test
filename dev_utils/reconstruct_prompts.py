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
    <run_dir>/prompts/03_asset_manifest.txt
    <run_dir>/prompts/04_scene_01_scene_001.txt
    ...

Currently supports: renpy pipeline only.
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

from pipelines.renpy.fns import (
    _build_bible_prompt,
    _build_scene_plan_prompt,
    _build_asset_manifest_prompt,
    _build_scene_script_prompts,
)


def reconstruct_renpy(run_dir: Path, out_dir: Path):
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


def _write(path: Path, content: str):
    path.write_text(content, encoding="utf-8")
    print(f"  wrote {path.name}")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("run_dir", help="Path to a pipeline run directory (must contain brief.json, bible.json, etc.)")
    args = parser.parse_args()

    run_dir = Path(args.run_dir).resolve()
    if not run_dir.exists():
        sys.exit(f"Not found: {run_dir}")

    out_dir = run_dir / "prompts"
    out_dir.mkdir(exist_ok=True)
    print(f"Reconstructing prompts for: {run_dir.name}")
    print(f"Output: {out_dir}\n")

    reconstruct_renpy(run_dir, out_dir)
    print("\nDone.")


if __name__ == "__main__":
    main()
