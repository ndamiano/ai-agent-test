"""
Quick story-graph visualiser.

Usage (from src/):
    python3 -m pipelines.renpy /path/to/story.json
"""

import argparse
import json

from pipelines.renpy.graph import assemble_story


def _print_tree(nid: str, nodes: dict, indent: int = 0, visited: set = None) -> None:
    if visited is None:
        visited = set()
    if nid in visited:
        print(" " * indent + f"[{nid}] (already shown above)")
        return
    visited.add(nid)

    n = nodes[nid]
    end_tag = f" [{n['end_type']}]" if n["end_type"] else ""
    reach_str = ""
    if n["type"] != "ending":
        reach_str = "  →  " + ", ".join(n["reachable_endings"])

    print(" " * indent + f"{nid}  ({n['type']}){end_tag}{reach_str}")
    for cid in n["child_ids"]:
        _print_tree(cid, nodes, indent + 4, visited)


def main() -> None:
    parser = argparse.ArgumentParser(description="Visualise the DAG assembled from a story outline")
    parser.add_argument("story", help="Path to a story.json produced by the story stage")
    args = parser.parse_args()

    story = json.loads(open(args.story, encoding="utf-8").read())
    graph = assemble_story(story)["graph"]
    nodes = graph["nodes"]

    print(f"\nQuestion: {story.get('central_question', '?')}\n")
    _print_tree(graph["root_id"], nodes)

    by_type = {}
    for n in nodes.values():
        by_type.setdefault(n["type"], []).append(n["id"])

    print("\nSummary")
    for t in ("root", "beat", "branch", "ending"):
        ids = by_type.get(t, [])
        print(f"  {t:<8} {len(ids):>2}  {ids}")
    print(f"  {'edges':<8} {len(graph['edges']):>2}")


if __name__ == "__main__":
    main()
