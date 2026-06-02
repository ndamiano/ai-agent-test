"""
Quick DAG visualiser.

Usage (from src/):
    python3 -m pipelines.renpy_vn_graph
    python3 -m pipelines.renpy_vn_graph --endings 4 --depth 7 --seed 42
"""

import argparse
from pipelines.renpy_vn_graph.graph import generate_dag


def _print_tree(nid: str, nodes: dict, indent: int = 0, visited: set = None) -> None:
    if visited is None:
        visited = set()
    if nid in visited:
        print(" " * indent + f"[{nid}] (already shown above)")
        return
    visited.add(nid)

    n = nodes[nid]
    end_tag  = f" [{n['end_type']}]" if n["end_type"] else ""
    if n["type"] not in ("ending",):
        reach = ", ".join(n["reachable_endings"])
        reach_str = f"  →  {reach}"
    else:
        reach_str = ""

    print(" " * indent + f"{nid}  ({n['type']}){end_tag}{reach_str}")
    for cid in n["child_ids"]:
        _print_tree(cid, nodes, indent + 4, visited)


def main() -> None:
    parser = argparse.ArgumentParser(description="Visualise a VN story DAG")
    parser.add_argument("--endings",     type=int,   default=4,    help="Number of endings")
    parser.add_argument("--depth",       type=int,   default=6,    help="Approximate depth root→ending")
    parser.add_argument("--min-good",    type=int,   default=2,    help="Minimum good endings")
    parser.add_argument("--merge",       type=float, default=0.3,  help="Merge probability (0.0–1.0)")
    parser.add_argument("--seed",        type=int,   default=None, help="RNG seed for reproducibility")
    args = parser.parse_args()

    dag   = generate_dag(
        num_endings=args.endings,
        depth=args.depth,
        min_good_endings=args.min_good,
        merge_probability=args.merge,
        seed=args.seed,
    )
    nodes = dag["nodes"]

    print(f"\nDAG  —  endings={args.endings}  depth={args.depth}  "
          f"merge={args.merge}  seed={args.seed}\n")
    _print_tree("root", nodes)

    by_type = {}
    for n in nodes.values():
        by_type.setdefault(n["type"], []).append(n["id"])

    print(f"\nSummary")
    for t in ("root", "beat", "branch", "ending"):
        ids = by_type.get(t, [])
        print(f"  {t:<8} {len(ids):>2}  {ids}")
    print(f"  {'edges':<8} {len(dag['edges']):>2}")


if __name__ == "__main__":
    main()
