"""
Usage:
    python run_pipeline.py                            # built-in example brief
    python run_pipeline.py brief.json                 # brief from file
    python run_pipeline.py brief.json --from scenes   # resume from a node
    python run_pipeline.py --list                     # list nodes and stages
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from engine.pipeline_runner import PipelineRunner
from pipelines.renpy.pipeline import RENPY_PIPELINE

EXAMPLE_BRIEF = {
    "title": "The Space Between",
    "genre": "romance",
    "tone": "bittersweet",
    "setting": "modern city",
    "notes": "Two old friends reunite after years apart. He returned because his father died. She never left.",
    "length": "short",
}

_LENGTH_CONFIG = {
    "short":  {"character_count": "2",   "scene_count": "3-4",  "lines_per_scene": "3-4"},
    "medium": {"character_count": "3",   "scene_count": "5-7",  "lines_per_scene": "5-7"},
    "long":   {"character_count": "3-4", "scene_count": "8-12", "lines_per_scene": "6-10"},
}


def _enrich_brief(brief: dict) -> dict:
    length = brief.get("length", "medium")
    config = _LENGTH_CONFIG.get(length, _LENGTH_CONFIG["medium"])
    return {**config, **brief}  # brief keys win so callers can override explicitly


def main():
    args = sys.argv[1:]

    if "--list" in args:
        print(f"Pipeline: {RENPY_PIPELINE.name}")
        for node in RENPY_PIPELINE.nodes:
            for stage in node.stages:
                print(f"  node={node.id:12}  stage={stage.id}")
        return

    brief_file = next((a for a in args if not a.startswith("--")), None)
    if brief_file:
        brief = json.loads(Path(brief_file).read_text(encoding="utf-8"))
    else:
        print("[run_pipeline] No brief file provided — using built-in example.")
        brief = EXAMPLE_BRIEF

    safe_title = brief.get("title", "untitled").lower().replace(" ", "_")
    runner = PipelineRunner(working_dir=str(Path("runs") / safe_title))

    from_idx = args.index("--from") if "--from" in args else -1
    brief = _enrich_brief(brief)

    if from_idx >= 0 and from_idx + 1 < len(args):
        node_id = args[from_idx + 1]
        print(f"[run_pipeline] Resuming from node: {node_id}")
        runner.run_from(RENPY_PIPELINE, brief, node_id)
    else:
        runner.run(RENPY_PIPELINE, brief)


if __name__ == "__main__":
    main()
