"""
Assemble the story DAG from a top-down story outline.

The outline (story.json) is written by the LLM with full visibility of the
whole story: endings first, then a trunk, then one arm per commitment-choice
option. This module deterministically converts that outline into the graph
and beat_map shapes the downstream stages consume.

Topology is branch-and-bottleneck:

    root → beat … → branch (commitment) → arm scenes … → [branch (crisis) →] endings

Node types:
  root    — first scene, single entry point
  beat    — linear scene, one child
  branch  — choice scene, ends in a menu
  ending  — leaf scene
"""

from typing import Dict, List


def _propagate_reachable(nodes: Dict[str, dict], topo: List[str]) -> None:
    for nid in reversed(topo):
        node = nodes[nid]
        if node["type"] == "ending":
            node["reachable_endings"] = [nid]
            continue
        reach: List[str] = []
        for cid in node["child_ids"]:
            for eid in nodes[cid]["reachable_endings"]:
                if eid not in reach:
                    reach.append(eid)
        node["reachable_endings"] = reach


def assemble_story(story: dict) -> dict:
    """Convert a story outline into {"graph": ..., "beat_map": ...}."""
    endings = story.get("endings", [])
    trunk   = story.get("trunk", [])
    arms    = story.get("arms", [])

    if len(trunk) < 2:
        raise ValueError("story trunk needs at least 2 scenes (opening + commitment choice)")
    if len(arms) < 2:
        raise ValueError("story needs at least 2 arms")
    if not endings:
        raise ValueError("story has no endings")

    nodes: Dict[str, dict] = {}
    beat_map: Dict[str, dict] = {}
    order: List[str] = []
    beat_counter = [0]
    branch_counter = [0]

    def _add(nid: str, ntype: str, scene: dict, end_type: str | None = None) -> str:
        nodes[nid] = {
            "id": nid, "type": ntype, "end_type": end_type,
            "parent_ids": [], "child_ids": [],
            "reachable_endings": [], "content": None,
        }
        beat_map[nid] = scene
        order.append(nid)
        return nid

    def _beat_id() -> str:
        beat_counter[0] += 1
        return f"beat_{beat_counter[0]:03d}"

    def _branch_id() -> str:
        branch_counter[0] += 1
        return f"branch_{branch_counter[0]:03d}"

    def _link(parent: str, child: str) -> None:
        nodes[parent]["child_ids"].append(child)
        nodes[child]["parent_ids"].append(parent)

    def _scene_beat(scene: dict, choice_labels: List[str] | None = None) -> dict:
        return {
            "summary":            scene.get("summary", ""),
            "dramatic_purpose":   scene.get("whats_new", ""),
            "scene_type":         scene.get("scene_type", ""),
            "when":               scene.get("when", ""),
            "location_id":        scene.get("location_id", "location"),
            "emotional_tone":     scene.get("emotional_tone", "tense"),
            "characters_present": scene.get("characters_present", []),
            "choice_labels":      choice_labels or [],
        }

    # --- trunk: root → beats → commitment branch -----------------------
    options = story.get("commitment_choice", {}).get("options", [])
    commitment_labels = [o.get("label", f"Path {i+1}") for i, o in enumerate(options)]

    trunk_ids: List[str] = []
    for i, scene in enumerate(trunk):
        is_last = i == len(trunk) - 1
        if i == 0 and not is_last:
            nid = _add("root", "root", _scene_beat(scene))
        elif is_last:
            nid = _add(_branch_id(), "branch", _scene_beat(scene, commitment_labels))
        else:
            nid = _add(_beat_id(), "beat", _scene_beat(scene))
        if trunk_ids:
            _link(trunk_ids[-1], nid)
        trunk_ids.append(nid)
    commitment_id = trunk_ids[-1]

    # --- endings (nodes created up front so arms can link to them) -----
    ending_ids: List[str] = []
    for e in endings:
        eid = e.get("id") or f"ending_{len(ending_ids)+1:03d}"
        nodes[eid] = {
            "id": eid, "type": "ending", "end_type": e.get("end_type", "neutral"),
            "parent_ids": [], "child_ids": [],
            "reachable_endings": [], "content": None,
        }
        beat_map[eid] = e
        ending_ids.append(eid)
    ending_set = set(ending_ids)

    # --- arms -----------------------------------------------------------
    arm_order: List[str] = []
    for arm in arms:
        arm_endings = [eid for eid in arm.get("ending_ids", []) if eid in ending_set]
        if not arm_endings:
            raise ValueError("arm has no valid ending_ids")
        scenes = arm.get("scenes", [])
        crisis_labels = arm.get("crisis_labels") or [
            beat_map[eid].get("title", f"Choice {i+1}") for i, eid in enumerate(arm_endings)
        ]

        prev = commitment_id
        for i, scene in enumerate(scenes):
            is_last = i == len(scenes) - 1
            if is_last and len(arm_endings) > 1:
                nid = _add(_branch_id(), "branch", _scene_beat(scene, crisis_labels))
            else:
                nid = _add(_beat_id(), "beat", _scene_beat(scene))
            _link(prev, nid)
            arm_order.append(nid)
            prev = nid

        if prev == commitment_id:
            raise ValueError("arm has no scenes")
        if len(arm_endings) > 1:
            for eid in arm_endings:
                _link(prev, eid)
        else:
            _link(prev, arm_endings[0])

    topo = trunk_ids + arm_order + ending_ids
    _propagate_reachable(nodes, topo)

    edges = [
        {"from": n["id"], "to": cid, "label": ""}
        for nid in topo for n in [nodes[nid]] for cid in n["child_ids"]
    ]

    graph = {
        "nodes": {nid: nodes[nid] for nid in topo},
        "edges": edges,
        "topological_order": topo,
        "ending_ids": ending_ids,
        "root_id": topo[0],
    }
    return {"graph": graph, "beat_map": beat_map}
