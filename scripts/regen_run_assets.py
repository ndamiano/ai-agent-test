#!/usr/bin/env python3
"""Regenerate a finished run's assets and recompile — a before/after harness for the asset-gen
work, run against the games already in ~/output/runs WITHOUT a fresh LLM build.

Pipeline:
  1. (unless --bg-only / --no-tag) LLM-tag every spoken line in nodes.json with an emotion, so
     existing runs (authored before emotions existed) exercise the new expression variants.
  2. Regenerate art: full two-pass character gen (neutral base + img2img expression variants) +
     backgrounds, or just backgrounds with --bg-only.
  3. Recompile the run (renpy) so you can open game_output and see the result.

Usage:
  python scripts/regen_run_assets.py <run_id> [--emotions-only] [--bg-only] [--no-tag]
  python scripts/regen_run_assets.py --list

Needs ComfyUI running for real art (placeholders otherwise). Run from the repo root with the venv
active.
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

_RUNS_DIR = Path.home() / "output" / "runs"
_PROMPTS = Path(__file__).resolve().parent.parent / "src" / "maestro" / "prompts"
_PROMPT = _PROMPTS / "tag_emotions.txt"


def _tag_emotions(run_dir: Path) -> None:
    """Backfill nodes.json: ask a cheap LLM pass for one expression per spoken line."""
    from llm_clients.inference import PipelineAgent, JSON_SYSTEM, json_with_correction
    from renpy.templating import render_template
    from maestro.ir_assemble import EMOTIONS

    nodes_path = run_dir / "nodes.json"
    comp = json.loads(nodes_path.read_text())
    nodes = comp.get("nodes", {})
    valid = set(EMOTIONS)

    for nid, node in nodes.items():
        spoken = [(i, ln) for i, ln in enumerate(node.get("lines", []))
                  if isinstance(ln, dict) and ln.get("speaker")]
        if not spoken:
            continue
        listing = "\n".join(f'{i}. {ln["speaker"]}: {ln["text"]}' for i, ln in spoken)
        prompt = render_template(_PROMPT, {"lines": listing})
        agent = PipelineAgent(JSON_SYSTEM, max_tokens=600)
        res = json_with_correction(agent, prompt, "tag_emotions", attempts=2) or {}
        emotions = res.get("emotions", []) if isinstance(res, dict) else []
        for (i, ln), emo in zip(spoken, emotions):
            if emo in valid and emo != "neutral":
                ln["emotion"] = emo
        print(f"    [emotions] {nid}: {[ln.get('emotion', 'neutral') for _, ln in spoken]}")

    nodes_path.write_text(json.dumps(comp, indent=2))


def _tag_locations(run_dir: Path) -> None:
    """Backfill nodes.json: pick a background per node so the scene shows a setting (older runs
    left every node's `location` empty, so no `scene` ever renders)."""
    from llm_clients.inference import PipelineAgent, JSON_SYSTEM, json_with_correction
    from renpy.templating import render_template

    manifest = json.loads((run_dir / "asset_manifest.json").read_text())
    backgrounds = manifest.get("backgrounds", [])
    if not backgrounds:
        print("    [locations] no backgrounds in manifest")
        return
    bg_ids = {b["id"] for b in backgrounds}
    menu = "\n".join(f'{b["id"]} — {b.get("description", "")[:160]}' for b in backgrounds)
    default = backgrounds[0]["id"]

    nodes_path = run_dir / "nodes.json"
    comp = json.loads(nodes_path.read_text())
    nodes = comp.get("nodes", {})

    for nid, node in nodes.items():
        spoken = [ln for ln in node.get("lines", []) if isinstance(ln, dict) and ln.get("text")]
        listing = "\n".join(f'{ln.get("speaker") or "(narration)"}: {ln["text"]}'
                            for ln in spoken[:12])
        prompt = render_template(_PROMPTS / "tag_location.txt",
                                 {"backgrounds": menu, "lines": listing})
        agent = PipelineAgent(JSON_SYSTEM, max_tokens=200)
        res = json_with_correction(agent, prompt, "tag_location", attempts=2) or {}
        loc = res.get("location") if isinstance(res, dict) else None
        node["location"] = loc if loc in bg_ids else default
        print(f"    [locations] {nid}: {node['location']}")

    nodes_path.write_text(json.dumps(comp, indent=2))


def _regen_backgrounds(run_dir: Path, artifact: dict) -> None:
    """Regenerate only the backgrounds (for evaluating a swapped scene checkpoint in isolation)."""
    import shutil
    from tools.comfyui_tools import build_background_job, vram_bracket, run_jobs

    manifest = artifact.get("asset_manifest", {}) or {}
    images_dir = run_dir / "game_output" / "game" / "images"
    images_dir.mkdir(parents=True, exist_ok=True)
    bgs = manifest.get("backgrounds", [])
    if not bgs:
        print("    [bg] no backgrounds declared")
        return
    jobs = [build_background_job(bg.get("description", bg.get("name", bg["id"]))) for bg in bgs]
    with vram_bracket():
        results = run_jobs(jobs)
    for bg, result in zip(bgs, results):
        dest = images_dir / bg["image_file"]
        if result.get("success") and result.get("saved_paths"):
            shutil.copy2(result["saved_paths"][0], dest)
            print(f"    [bg] ok: {bg['image_file']}")
        else:
            print(f"    [bg] failed: {bg['image_file']} ({result.get('error', 'unknown')})")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("run_id", nargs="?", help="run dir name under ~/output/runs")
    ap.add_argument("--list", action="store_true", help="list available run ids and exit")
    ap.add_argument("--emotions-only", action="store_true",
                    help="tag emotions + regenerate sprites/bgs (default behaviour)")
    ap.add_argument("--bg-only", action="store_true",
                    help="regenerate only backgrounds (no emotion tagging, no sprite gen)")
    ap.add_argument("--no-tag", action="store_true",
                    help="skip the LLM emotion/location backfill; use nodes.json as-is")
    ap.add_argument("--no-gen", action="store_true",
                    help="skip asset generation; only (re)tag nodes and recompile")
    args = ap.parse_args()

    if args.list or not args.run_id:
        runs = sorted(p.name for p in _RUNS_DIR.iterdir() if p.is_dir()) if _RUNS_DIR.exists() else []
        print(f"runs in {_RUNS_DIR}:")
        for r in runs:
            print(f"  {r}")
        return 0

    run_dir = _RUNS_DIR / args.run_id
    if not (run_dir / "nodes.json").exists():
        print(f"error: {run_dir} has no nodes.json (not a finished run)")
        return 1

    from maestro.state import RunState
    from renpy.fns import generate_images
    from renpy.compiler import compile_renpy

    if not args.bg_only and not args.no_tag:
        print("[1/3] tagging emotions + locations...")
        _tag_emotions(run_dir)
        _tag_locations(run_dir)

    artifact = RunState(run_dir).load_artifact()

    print("[2/3] generating assets...")
    if args.no_gen:
        print("    (skipped — --no-gen)")
    elif args.bg_only:
        _regen_backgrounds(run_dir, artifact)
    else:
        generate_images(artifact, run_dir)

    print("[3/3] recompiling...")
    result = compile_renpy(run_dir, distribute=False)
    ok = result.get("lint", {}).get("error_count") in (0, None)
    print(f"compile: {'ok' if ok else 'LINT ERRORS'} -> {run_dir / 'game_output'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
