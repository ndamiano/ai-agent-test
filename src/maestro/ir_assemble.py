"""Lift the decomposed on-disk components into one schema-valid Game IR dict.

The agent authors the game as separate components (premise + asset_manifest + nodes [+ places])
so each write stays small and the context stays constant. The IR linter (ir_crossref) and every
engine backend (renpy.ir_vn / ir_pnc, web, …) want ONE whole IR document. assemble_ir is the seam:
a pure, engine-neutral projection that maps premise→characters, the body components→nodes/places,
and the body's declared state→top-level flags/variables/items/goal/start. It lives in maestro core
(next to ir_crossref) so every engine depends on it, not on each other.
"""

from typing import Dict, List


def _ir_genre(genre: str) -> str:
    return {"vn": "visual_novel", "point_and_click": "point_and_click",
            "rpg": "rpg"}.get(genre, "visual_novel")


EMOTIONS = ("neutral", "happy", "sad", "angry", "surprised", "worried")


def expression_file(image_file: str, emotion: str) -> str:
    """Per-emotion sprite filename derived from a character's base image_file. Neutral keeps the
    original file (so the existing single-sprite path is untouched); other emotions get a
    `<stem>_<emotion>.png` sibling. Shared by the IR projection and the asset pipeline so the
    file the compiler references is the file generation writes."""
    if emotion == "neutral":
        return image_file
    stem = image_file.rsplit(".", 1)[0]
    return f"{stem}_{emotion}.png"


def used_emotions(char_id: str, nodes: List[Dict]) -> List[str]:
    """Distinct emotions this character actually speaks with, in EMOTIONS order, always
    including neutral. Bounds how many sprite variants get generated per cast member."""
    seen = {"neutral"}
    for node in nodes:
        lines = node.get("lines")
        if not isinstance(lines, list):
            continue  # malformed input is the schema gate's job to reject, not ours to crash on
        for line in lines:
            if (isinstance(line, dict) and line.get("speaker") == char_id
                    and line.get("emotion") in EMOTIONS):
                seen.add(line["emotion"])
    return [e for e in EMOTIONS if e in seen]


def _characters(premise: Dict, manifest: Dict, nodes: List[Dict]) -> List[Dict]:
    sprites = {c.get("id"): c.get("image_file")
               for c in manifest.get("characters", []) if c.get("id")}
    out = []
    for c in premise.get("characters", []):
        if not c.get("id"):
            continue
        ch = {"id": c["id"], "name": c.get("name") or c["id"]}
        base = sprites.get(c["id"])
        if base:
            ch["sprite"] = base
            emotions = used_emotions(c["id"], nodes)
            if emotions != ["neutral"]:
                ch["expressions"] = {e: expression_file(base, e) for e in emotions}
        out.append(ch)
    return out


def _backgrounds(manifest: Dict) -> List[Dict]:
    return [{"id": bg["id"], "image_file": bg["image_file"]}
            for bg in manifest.get("backgrounds", [])
            if bg.get("id") and bg.get("image_file")]


def assemble_ir(artifact: Dict, genre: str = "vn") -> Dict:
    """Build the full IR dict from the component artifact. `genre` is the spec genre
    ('vn' | 'point_and_click' | 'rpg'); it is mapped to the IR genre enum."""
    premise = artifact.get("premise", {}) or {}
    manifest = artifact.get("asset_manifest", {}) or {}
    nodes_comp = artifact.get("nodes", {}) or {}
    places_comp = artifact.get("places", {}) or {}
    brief = artifact.get("brief", {}) or {}

    node_ids = nodes_comp.get("node_ids", []) or []
    nodes_map = nodes_comp.get("nodes", {}) or {}

    nodes = [{"id": nid, **nodes_map.get(nid, {})} for nid in node_ids]
    ir: Dict = {
        "version": "0.1",
        "genre": _ir_genre(genre),
        "characters": _characters(premise, manifest, nodes),
        "nodes": nodes,
    }

    backgrounds = _backgrounds(manifest)
    if backgrounds:
        ir["backgrounds"] = backgrounds

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
