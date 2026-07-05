"""Lift the decomposed on-disk components into one schema-valid Game IR dict.

The agent authors the game as separate components (characters + asset_manifest + nodes [+ places])
so each write stays small and the context stays constant. The IR linter (ir_crossref) and every
engine backend (renpy.ir_vn / ir_pnc, web, …) want ONE whole IR document. assemble_ir is the seam:
a pure, engine-neutral projection that maps the characters component→IR characters, the body
components→nodes/places,
and the body's declared state→top-level flags/variables/items/goal/start. It lives in maestro core
(next to ir_crossref) so every engine depends on it, not on each other.
"""

from typing import Dict, List


EMOTIONS = ("neutral", "happy", "sad", "angry", "surprised", "worried")

# Narration is speaker:null; the model often writes a string ("narration"/"narrator") instead,
# which then reads as an undeclared character at crossref/compile. Normalized at write time AND
# here at assembly (the backstop), so this never reaches the reference gate.
NARRATION_ALIASES = frozenset({"narration", "narrator", "the narrator", "none", "null", "narrate", ""})


def is_narration_speaker(s) -> bool:
    return isinstance(s, str) and s.strip().lower() in NARRATION_ALIASES


def expression_file(image_file: str, emotion: str) -> str:
    """Per-emotion sprite filename derived from a character's base image_file. Neutral keeps the
    original file (so the existing single-sprite path is untouched); other emotions get a
    `<stem>_<emotion>.png` sibling. Shared by the IR projection and the asset pipeline so the
    file the compiler references is the file generation writes."""
    if emotion == "neutral":
        return image_file
    stem = image_file.rsplit(".", 1)[0]
    return f"{stem}_{emotion}.png"


def voice_file(node_id: str, line_index: int) -> str:
    """Per-line voice clip filename, derived from node id + line position. Shared by the IR
    projection (the `voice` reference), the TTS pipeline (what it writes), and the placeholder
    backfill (so a missing clip still resolves) — same contract as expression_file for sprites."""
    return f"vo_{node_id}_{line_index}.wav"


def voiced_lines(nodes: List[Dict]):
    """Yield (node_id, line_index, line) for every spoken (non-narration) line across nodes.
    The single source of truth for WHICH lines get a clip, so the projection and the pipeline
    iterate identically."""
    for node in nodes:
        lines = node.get("lines")
        if not isinstance(lines, list):
            continue
        for i, line in enumerate(lines):
            if isinstance(line, dict) and not is_narration_speaker(line.get("speaker")) \
                    and line.get("speaker"):
                yield node["id"], i, line


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


def _characters(cast: Dict, manifest: Dict, nodes: List[Dict]) -> List[Dict]:
    sprites = {c.get("id"): c.get("image_file")
               for c in manifest.get("characters", []) if c.get("id")}
    out = []
    for c in cast.get("characters", []):
        if not c.get("id"):
            continue
        ch = {"id": c["id"], "name": c.get("name") or c["id"]}
        for k in ("sex", "tts_voice"):
            if c.get(k):
                ch[k] = c[k]
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


def _scan_refs(obj, flags: set, variables: set) -> None:
    """Collect every flag/variable id REFERENCED anywhere in the IR — set_flag/clear_flag/flag for
    flags; var / set_var.var / add_var.var (covers conditions, effects, and a match's ante) for
    variables. A generic walk so it can't miss a location as new shapes are added."""
    if isinstance(obj, dict):
        for k in ("set_flag", "clear_flag", "flag"):
            if isinstance(obj.get(k), str):
                flags.add(obj[k])
        if isinstance(obj.get("var"), str):
            variables.add(obj["var"])
        for k in ("set_var", "add_var"):
            sv = obj.get(k)
            if isinstance(sv, dict) and isinstance(sv.get("var"), str):
                variables.add(sv["var"])
        for v in obj.values():
            _scan_refs(v, flags, variables)
    elif isinstance(obj, list):
        for v in obj:
            _scan_refs(v, flags, variables)


def assemble_ir(artifact: Dict) -> Dict:
    """Build the full IR dict from the component artifact. Presence-driven: a `places` component
    gives IR genre point_and_click (entry = start.place); otherwise visual_novel (entry =
    start.node). The components present decide the shape."""
    cast = artifact.get("characters", {}) or {}
    manifest = artifact.get("asset_manifest", {}) or {}
    nodes_comp = artifact.get("nodes", {}) or {}
    places_comp = artifact.get("places", {}) or {}
    brief = artifact.get("brief", {}) or {}

    node_ids = nodes_comp.get("node_ids", []) or []
    nodes_map = nodes_comp.get("nodes", {}) or {}
    has_places = bool(places_comp.get("place_ids"))
    is_rpg = has_places and any(
        isinstance(p, dict) and p.get("kind") in ("world_map", "town", "interior")
        for p in (places_comp.get("places") or {}).values())

    # `beat` is authoring provenance (which story beat a scene realizes — drives the
    # beats_realized done-condition on the on-disk component); it's not runtime IR, and the schema
    # is additionalProperties:false, so drop it here.
    nodes = [{k: v for k, v in {"id": nid, **nodes_map.get(nid, {})}.items() if k != "beat"}
             for nid in node_ids]
    # A line may carry emotion: null (the model spelling out "neutral"); the schema enum has no
    # null, so drop the key — absent == neutral.
    for n in nodes:
        for ln in n.get("lines", []) or []:
            if isinstance(ln, dict) and ln.get("emotion") is None:
                ln.pop("emotion", None)
            if isinstance(ln, dict) and is_narration_speaker(ln.get("speaker")):
                ln["speaker"] = None
    ir: Dict = {
        "version": "0.1",
        "genre": "rpg" if is_rpg else ("point_and_click" if has_places else "visual_novel"),
        "characters": _characters(cast, manifest, nodes),
        "nodes": nodes,
    }

    backgrounds = _backgrounds(manifest)
    if backgrounds:
        ir["backgrounds"] = backgrounds

    title = brief.get("title") or cast.get("title")
    if title:
        ir["meta"] = {"title": title}

    # Gameplay state is authored on the body components; merge it to the top level. The item
    # catalogue is its own `inventory` component.
    flags = list(nodes_comp.get("flags", []) or []) + list(places_comp.get("flags", []) or [])
    variables = (list(nodes_comp.get("variables", []) or [])
                 + list(places_comp.get("variables", []) or []))
    items = list((artifact.get("items", {}) or {}).get("items", []) or [])
    if flags:
        ir["flags"] = flags
    if variables:
        ir["variables"] = variables
    if items:
        ir["items"] = items

    # combat: the combat component holds the whole combat block as one document (the slices are
    # too interdependent to author piecemeal). Lift each present slice to the top level + the
    # combat_model projection hint (presence-driven).
    combat_comp = artifact.get("combat", {}) or {}
    if combat_comp.get("combat_model"):
        ir["combat_model"] = combat_comp["combat_model"]
    for slice_key in ("stats", "statuses", "abilities", "combatants", "encounters"):
        rows = combat_comp.get(slice_key)
        if rows:
            ir[slice_key] = rows
    if combat_comp.get("progression"):
        ir["progression"] = combat_comp["progression"]

    if has_places:
        place_ids = places_comp.get("place_ids", []) or []
        places_map = places_comp.get("places", {}) or {}
        ir["places"] = [{"id": pid, **places_map.get(pid, {})} for pid in place_ids]
        start_place = places_comp.get("start_place") or (place_ids[0] if place_ids else None)
        ir["start"] = {"place": start_place} if start_place else {}
        start_spawn = places_comp.get("start_spawn")
        if start_place and isinstance(start_spawn, dict) and isinstance(start_spawn.get("cell"), dict):
            ir["start"]["spawn"] = start_spawn
        goal = places_comp.get("goal")
        if isinstance(goal, dict):
            # The IR goal is a CONDITION. A flag goal maps to {flag: id}; a goal already in
            # condition shape passes through. A room goal ("reach room X") isn't expressible as a
            # condition, and an endless game has no win — drop those rather than emit invalid IR.
            _COND_KEYS = ("flag", "var", "item", "all", "any", "not")
            if goal.get("type") == "flag" and goal.get("id"):
                ir["goal"] = {"flag": goal["id"]}
            elif any(k in goal for k in _COND_KEYS):
                ir["goal"] = goal
    else:
        start_node = nodes_comp.get("start") or (node_ids[0] if node_ids else None)
        ir["start"] = {"node": start_node} if start_node else {}

    # Auto-declare any FLAG referenced anywhere but never explicitly declared (default false) — a
    # referenced-but-unset flag is benign (a gate that simply never fires), so a forgotten flag
    # declaration shouldn't be a hard compile failure. Variables are NOT auto-declared: a var needs
    # a real default (gold starts at 100, not 0) and a typo'd var id should be CAUGHT, not silently
    # invented — crossref flags it and the model declares it via set_places_meta.
    ref_flags: set = set()
    _scan_refs(ir, ref_flags, set())
    missing_flags = [f for f in sorted(ref_flags) if f not in set(ir.get("flags", []))]
    if missing_flags:
        ir["flags"] = list(ir.get("flags", [])) + missing_flags

    return ir
