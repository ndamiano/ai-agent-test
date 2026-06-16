"""
Single-scene dev CLI: regenerate one node from an existing pipeline run with
the current prompts, without re-running the whole pipeline.

Usage (from src/, venv active):
    python -m renpy.scene <run_dir>             # list nodes
    python -m renpy.scene <run_dir> <node_id>   # regenerate one node

Workflow: tweak a prompt in renpy/prompts/, rerun the same node,
compare. The sketch is printed before the script so both halves of the
generation are visible.
"""

import json
import sys
from pathlib import Path

from renpy import fns


def _load_run(run_dir: str) -> tuple:
    run = Path(run_dir)
    for name in ("premise.json", "graph.json", "beat_map.json"):
        if not (run / name).exists():
            raise FileNotFoundError(f"{run / name} not found — not a pipeline run dir?")
    premise  = json.loads((run / "premise.json").read_text())
    graph    = json.loads((run / "graph.json").read_text())
    beat_map = json.loads((run / "beat_map.json").read_text())
    beat_map = beat_map.get("beat_map", beat_map)
    return premise, graph, beat_map


def _list_nodes(graph: dict, beat_map: dict) -> None:
    for nid in graph.get("topological_order", []):
        node = graph["nodes"].get(nid, {})
        summary = beat_map.get(nid, {}).get("summary", "")
        print(f"  {nid:<14} {node.get('type', '?'):<7} {summary[:90]}")


def main(argv: list) -> int:
    if not argv:
        print(__doc__.strip())
        return 2

    premise, graph, beat_map = _load_run(argv[0])

    if len(argv) == 1:
        _list_nodes(graph, beat_map)
        return 0

    nid = argv[1]
    if nid not in graph.get("nodes", {}):
        print(f"unknown node id: {nid}\n")
        _list_nodes(graph, beat_map)
        return 2

    orig_sketch = fns._generate_scene_sketch

    def echoing_sketch(*args, **kwargs):
        sketch = orig_sketch(*args, **kwargs)
        print("--- sketch " + "-" * 50)
        print(json.dumps(sketch, indent=2))
        return sketch

    fns._generate_scene_sketch = echoing_sketch
    try:
        script = fns.generate_single_node(premise, graph, beat_map, nid)
    finally:
        fns._generate_scene_sketch = orig_sketch

    print("--- script " + "-" * 50)
    print(script)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
