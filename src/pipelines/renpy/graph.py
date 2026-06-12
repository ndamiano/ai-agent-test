"""
Procedural DAG generator for branching visual novels.

Builds a directed acyclic graph (root → ... → endings) with no LLM involvement.
The shape is determined entirely by parameters; story content is filled in later.

Node types:
  root    — single entry point
  beat    — linear scene, one child (no player choice)
  branch  — choice point, two or more children
  ending  — leaf node, game-over scene
"""

import random
from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class VNNode:
    id: str
    type: str                          # root | beat | branch | ending
    end_type: Optional[str]            # good | bad | neutral  (endings only)
    parent_ids: List[str] = field(default_factory=list)
    child_ids: List[str] = field(default_factory=list)
    reachable_endings: List[str] = field(default_factory=list)


def _assign_end_types(num_endings: int, min_good: int) -> List[str]:
    if min_good > num_endings:
        raise ValueError("min_good_endings cannot exceed num_endings")
    types = ["good"] * min_good
    pool = ["good", "neutral", "bad"]
    for _ in range(num_endings - min_good):
        types.append(random.choice(pool))
    random.shuffle(types)
    return types


def _topological_sort(nodes: Dict[str, VNNode]) -> List[str]:
    """Kahn's algorithm — returns nodes from root to endings."""
    in_degree = {nid: len(n.parent_ids) for nid, n in nodes.items()}
    queue = [nid for nid, d in in_degree.items() if d == 0]
    order: List[str] = []
    while queue:
        nid = queue.pop(0)
        order.append(nid)
        for child_id in nodes[nid].child_ids:
            in_degree[child_id] -= 1
            if in_degree[child_id] == 0:
                queue.append(child_id)
    return order


def _merge_pair(
    nodes: Dict[str, VNNode],
    left_id: str,
    right_id: str,
    counter: List[int],
) -> str:
    """Create a branch node that is the parent of left and right."""
    counter[0] += 1
    nid = f"branch_{counter[0]:03d}"
    reach = nodes[left_id].reachable_endings + nodes[right_id].reachable_endings
    node = VNNode(
        id=nid, type="branch", end_type=None,
        child_ids=[left_id, right_id],
        reachable_endings=reach,
    )
    nodes[nid] = node
    nodes[left_id].parent_ids.append(nid)
    nodes[right_id].parent_ids.append(nid)
    return nid


def _linear_beat(
    nodes: Dict[str, VNNode],
    child_id: str,
    counter: List[int],
) -> str:
    """Create a beat node that is the linear parent of child."""
    counter[0] += 1
    nid = f"beat_{counter[0]:03d}"
    node = VNNode(
        id=nid, type="beat", end_type=None,
        child_ids=[child_id],
        reachable_endings=nodes[child_id].reachable_endings[:],
    )
    nodes[nid] = node
    nodes[child_id].parent_ids.append(nid)
    return nid


def _collapse_frontier(
    nodes: Dict[str, VNNode],
    frontier: List[str],
    counter: List[int],
) -> List[str]:
    """Merge adjacent pairs until frontier is half the size (or 1)."""
    next_f: List[str] = []
    for i in range(0, len(frontier), 2):
        if i + 1 < len(frontier):
            nid = _merge_pair(nodes, frontier[i], frontier[i + 1], counter)
            next_f.append(nid)
        else:
            next_f.append(frontier[i])
    return next_f


def generate_dag(
    num_endings: int = 4,
    depth: int = 6,
    min_good_endings: int = 2,
    merge_probability: float = 0.3,
    seed: Optional[int] = None,
) -> dict:
    """
    Build a story DAG and return a serialisable dict.

    Parameters
    ----------
    num_endings        : number of leaf (ending) nodes
    depth              : approximate number of layers root→ending
    min_good_endings   : guaranteed minimum "good" endings
    merge_probability  : per-layer probability that adjacent paths reconverge
    seed               : optional RNG seed for reproducibility

    Returns
    -------
    {
      nodes: {id: {id, type, end_type, parent_ids, child_ids, reachable_endings, content}},
      edges: [{from, to, label}],
      topological_order: [id, ...],
      ending_ids: [id, ...],
      root_id: "root",
    }
    """
    if seed is not None:
        random.seed(seed)

    nodes: Dict[str, VNNode] = {}
    counter = [0]

    # --- endings -------------------------------------------------------
    end_types = _assign_end_types(num_endings, min_good_endings)
    ending_ids: List[str] = []
    for et in end_types:
        counter[0] += 1
        nid = f"ending_{counter[0]:03d}"
        node = VNNode(id=nid, type="ending", end_type=et, reachable_endings=[nid])
        nodes[nid] = node
        ending_ids.append(nid)

    # --- build backwards: endings → root -------------------------------
    # frontier = the "current generation" being connected to new parents
    frontier = list(ending_ids)
    target_layers = max(depth - 1, 1)  # -1 reserves one slot for root

    for layer_idx in range(target_layers):
        layers_left = target_layers - layer_idx
        must_converge = len(frontier) > layers_left

        next_frontier: List[str] = []
        i = 0
        while i < len(frontier):
            if (
                i + 1 < len(frontier)
                and (must_converge or random.random() < merge_probability)
            ):
                nid = _merge_pair(nodes, frontier[i], frontier[i + 1], counter)
                next_frontier.append(nid)
                i += 2
            else:
                nid = _linear_beat(nodes, frontier[i], counter)
                next_frontier.append(nid)
                i += 1

        frontier = next_frontier

    # --- final collapse to single root child ---------------------------
    while len(frontier) > 1:
        frontier = _collapse_frontier(nodes, frontier, counter)

    # --- root node -----------------------------------------------------
    root_child = frontier[0]
    root = VNNode(
        id="root", type="root", end_type=None,
        child_ids=[root_child],
        reachable_endings=nodes[root_child].reachable_endings[:],
    )
    nodes["root"] = root
    nodes[root_child].parent_ids.append("root")

    # --- serialise -----------------------------------------------------
    topo = _topological_sort(nodes)
    edges = [
        {"from": n.id, "to": cid, "label": ""}
        for n in nodes.values()
        for cid in n.child_ids
    ]

    return {
        "nodes": {
            nid: {
                "id": nid,
                "type": n.type,
                "end_type": n.end_type,
                "parent_ids": n.parent_ids,
                "child_ids": n.child_ids,
                "reachable_endings": n.reachable_endings,
                "content": None,
            }
            for nid, n in nodes.items()
        },
        "edges": edges,
        "topological_order": topo,
        "ending_ids": ending_ids,
        "root_id": "root",
    }
