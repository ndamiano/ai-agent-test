"""Lift the decomposed on-disk components into one schema-valid Game IR dict.

The agent authors the game as separate components (premise + asset_manifest + nodes [+ places])
so each write stays small and the context stays constant. The IR linter (ir_crossref) and the
IR→Ren'Py compilers (ir_vn / ir_pnc) want ONE whole IR document. assemble_ir is the seam: a pure
projection that maps premise→characters, the body components→nodes/places, and the body's
declared state→top-level flags/variables/items/goal/start.
"""

from typing import Dict, List


def _ir_genre(genre: str) -> str:
    return {"vn": "visual_novel", "point_and_click": "point_and_click",
            "rpg": "rpg"}.get(genre, "visual_novel")


def _characters(premise: Dict) -> List[Dict]:
    out = []
    for c in premise.get("characters", []):
        if not c.get("id"):
            continue
        out.append({"id": c["id"], "name": c.get("name") or c["id"]})
    return out


def assemble_ir(artifact: Dict, genre: str = "vn") -> Dict:
    """Build the full IR dict from the component artifact. `genre` is the spec genre
    ('vn' | 'point_and_click' | 'rpg'); it is mapped to the IR genre enum."""
    premise = artifact.get("premise", {}) or {}
    nodes_comp = artifact.get("nodes", {}) or {}
    places_comp = artifact.get("places", {}) or {}
    brief = artifact.get("brief", {}) or {}

    node_ids = nodes_comp.get("node_ids", []) or []
    nodes_map = nodes_comp.get("nodes", {}) or {}

    ir: Dict = {
        "version": "0.1",
        "genre": _ir_genre(genre),
        "characters": _characters(premise),
        "nodes": [{"id": nid, **nodes_map.get(nid, {})} for nid in node_ids],
    }

    title = brief.get("title") or premise.get("title")
    if title:
        ir["meta"] = {"title": title}

    # Gameplay state is authored on the body components; merge it to the top level.
    flags = list(nodes_comp.get("flags", []) or []) + list(places_comp.get("flags", []) or [])
    variables = (list(nodes_comp.get("variables", []) or [])
                 + list(places_comp.get("variables", []) or []))
    items = list(nodes_comp.get("items", []) or []) + list(places_comp.get("items", []) or [])
    if flags:
        ir["flags"] = flags
    if variables:
        ir["variables"] = variables
    if items:
        ir["items"] = items

    if genre == "point_and_click":
        place_ids = places_comp.get("place_ids", []) or []
        places_map = places_comp.get("places", {}) or {}
        ir["places"] = [{"id": pid, **places_map.get(pid, {})} for pid in place_ids]
        start_place = places_comp.get("start_place") or (place_ids[0] if place_ids else None)
        ir["start"] = {"place": start_place} if start_place else {}
        if places_comp.get("goal"):
            ir["goal"] = places_comp["goal"]
    else:
        start_node = nodes_comp.get("start") or (node_ids[0] if node_ids else None)
        ir["start"] = {"node": start_node} if start_node else {}

    return ir
