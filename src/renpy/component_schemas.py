"""Structural schemas for the Game IR buildable components.

The agent authors component content itself, so it needs to know the exact shape assemble_ir/
compile consume — and writing the wrong shape must become an immediate, precise steering signal
rather than a crash later. SCHEMAS validates a component on write; SKELETONS shows the agent the
shape to author.

Injected into maestro.build_tools so maestro stays genre-agnostic. Validation is structural only
(fields the compiler needs); deep IR validity + reference integrity are owned by the JSON schema
and ir_crossref at compile.
"""

from typing import Callable, Dict, Optional

_END_TYPES = {"jump", "menu", "return", "end"}


def _v_premise(c: Dict) -> Optional[str]:
    chars = c.get("characters")
    if not isinstance(chars, list) or not chars:
        return "premise.characters must be a non-empty list of character objects"
    for i, ch in enumerate(chars):
        if not isinstance(ch, dict):
            return f"premise.characters[{i}] must be an object"
        if not ch.get("id"):
            return f"premise.characters[{i}] needs an 'id' (snake_case, e.g. 'evelyn')"
        if not ch.get("name"):
            return f"premise.characters[{i}] needs a 'name'"
    return None


def _v_asset_manifest(c: Dict) -> Optional[str]:
    for key in ("backgrounds", "characters", "cgs"):
        if not isinstance(c.get(key), list):
            return f"asset_manifest.{key} must be a list (use [] if none)"
    for i, bg in enumerate(c.get("backgrounds", [])):
        if not isinstance(bg, dict) or not bg.get("id"):
            return f"asset_manifest.backgrounds[{i}] needs an 'id' (e.g. 'bg_office')"
    for i, ch in enumerate(c.get("characters", [])):
        if not isinstance(ch, dict) or not ch.get("id"):
            return f"asset_manifest.characters[{i}] needs an 'id' matching a premise character id"
    if "items" in c:
        if not isinstance(c["items"], list):
            return "asset_manifest.items must be a list (use [] if none)"
        for i, it in enumerate(c["items"]):
            if not isinstance(it, dict) or not it.get("id"):
                return f"asset_manifest.items[{i}] needs an 'id' matching a places item id"
    if "title_card" in c and not isinstance(c["title_card"], dict):
        return "asset_manifest.title_card must be an object"
    return None


def _v_nodes(c: Dict) -> Optional[str]:
    node_ids = c.get("node_ids")
    nodes = c.get("nodes")
    if not isinstance(node_ids, list) or not node_ids:
        return "nodes.node_ids must be a non-empty list of node id strings"
    if not isinstance(nodes, dict):
        return "nodes.nodes must be an object mapping node_id -> {lines, end}"
    if "start" in node_ids:
        return ("do not use 'start' as a node id — the compiler adds 'label start' that "
                "jumps to node_ids[0]")
    for nid in node_ids:
        node = nodes.get(nid)
        if not isinstance(node, dict):
            return f"nodes.nodes is missing an object for node id '{nid}'"
        lines = node.get("lines")
        if not isinstance(lines, list) or not lines:
            return f"nodes.nodes['{nid}'].lines must be a non-empty list of {{speaker, text}}"
        end = node.get("end")
        if not isinstance(end, dict) or end.get("type") not in _END_TYPES:
            return f"nodes.nodes['{nid}'].end must have a 'type' in {sorted(_END_TYPES)}"
    return None


def _v_places(c: Dict) -> Optional[str]:
    place_ids = c.get("place_ids")
    places = c.get("places")
    if not isinstance(place_ids, list) or not place_ids:
        return "places.place_ids must be a non-empty list of place id strings"
    if not isinstance(places, dict):
        return "places.places must be an object mapping place_id -> {background, interactables}"
    if c.get("start_place") and c["start_place"] not in place_ids:
        return f"places.start_place {c['start_place']!r} is not in place_ids"
    if not isinstance(c.get("items", []), list):
        return "places.items must be a list (use [] if none)"
    for i, it in enumerate(c.get("items", [])):
        if not isinstance(it, dict) or not it.get("id"):
            return f"places.items[{i}] needs an 'id' (e.g. 'item_key')"
    goal = c.get("goal")
    if goal is not None and (not isinstance(goal, dict) or not goal.get("type") or not goal.get("id")):
        return "places.goal must be an object with 'type' ('flag' or 'room') and 'id'"
    for pid, place in places.items():
        if not isinstance(place, dict):
            return f"places.places[{pid!r}] must be an object"
        inter = place.get("interactables")
        if not isinstance(inter, list) or not inter:
            return f"places.places[{pid!r}].interactables must be a non-empty list"
        for j, h in enumerate(inter):
            if not isinstance(h, dict) or not h.get("id"):
                return f"places.places[{pid!r}].interactables[{j}] needs an 'id'"
            act = h.get("action")
            if not isinstance(act, dict) or not act.get("type"):
                return f"places.places[{pid!r}].interactables[{j}].action needs a 'type'"
    return None


SCHEMAS: Dict[str, Callable[[Dict], Optional[str]]] = {
    "premise": _v_premise,
    "asset_manifest": _v_asset_manifest,
    "nodes": _v_nodes,
    "places": _v_places,
}

# Which components each genre's spec carries — so skeleton_guide shows the agent the right
# shapes. vn = visual novel (dialogue graph); point_and_click = places + interactables +
# inventory, reusing `nodes` for NPC dialogue.
GENRE_COMPONENTS: Dict[str, list] = {
    "vn": ["premise", "asset_manifest", "nodes"],
    "point_and_click": ["premise", "asset_manifest", "nodes", "places"],
}


def validate_component(component_id: str, content) -> Optional[str]:
    fn = SCHEMAS.get(component_id)
    if fn is None:
        return None
    if not isinstance(content, dict):
        return f"{component_id} must be a JSON object"
    return fn(content)


SKELETONS: Dict[str, str] = {
    "premise": (
        '{\n'
        '  "central_question": "the dramatic question the endings answer differently",\n'
        '  "characters": [\n'
        '    {\n'
        '      "id": "<snake_case_id>",\n'
        '      "name": "<Display Name>",\n'
        '      "voice": "one line: vocabulary, sentence length, rhythm, what breaks under pressure",\n'
        '      "temperament": "2-4 words for how they carry themselves",\n'
        '      "drive": "what they want, plainly — a thing they would say out loud, NOT a goal for this plot",\n'
        '      "history": ["one concrete formative event", "a second, different event"],\n'
        '      "competencies": ["a concrete skill", "another"],\n'
        '      "example_lines": ["a line only they would say", "another in their voice"],\n'
        '      "color": "#c8ffc8"\n'
        '    }\n'
        '  ],\n'
        '  "endings": [ {"id": "ending_<slug>", "description": "..."} ]\n'
        '}\n'
        '// Invent ids/names/detail from THE REQUEST. Angle-bracket tokens are placeholders —\n'
        '//   never emit them literally, and do not reuse example ids from other prompts.\n'
        '// drive: legible beats exotic. history: 2-3 DISTINCT whole events, each new info.\n'
        '// example_lines: must sound DIFFERENT between characters — swap-test them.'
    ),
    "asset_manifest": (
        '{\n'
        '  "backgrounds": [ {"id": "bg_<place>", "image_file": "<place>.png", "description": "..."} ],\n'
        '  "characters":  [ {"id": "<same id as premise>", "image_file": "<id>.png", "description": "..."} ],\n'
        '  "items": [ {"id": "<same id as a places item>", "image_file": "<id>.png", "description": "..."} ],\n'
        '  "cgs": [],\n'
        '  "title_card": {"image_file": "title_card.png", "description": "..."}\n'
        '}\n'
        '// asset_manifest holds IMAGES, not speakers. character ids here MUST match\n'
        '//   premise.characters ids exactly. items hold inventory ICONS; their ids MUST match\n'
        '//   places items ids. Omit "items" (or use []) for a visual novel with no inventory.'
    ),
    "nodes": (
        '{\n'
        '  "node_ids": ["scene_01", "scene_02", "ending_<slug>"],  // do NOT include "start"\n'
        '  "nodes": {\n'
        '    "scene_01": {\n'
        '      "location": "bg_<place>",\n'
        '      "lines": [\n'
        '        {"speaker": "<char_a>", "text": "..."},\n'
        '        {"speaker": null, "text": "narration has speaker null"},\n'
        '        {"speaker": "<char_b>", "text": "...", "effects": [{"set_flag": "<flag>"}]}\n'
        '      ],\n'
        '      "end": {"type": "menu", "choices": [\n'
        '        {"text": "choice one", "target": "scene_02"},\n'
        '        {"text": "choice two", "target": "ending_<slug>", "requires": {"var": "<v>", "op": ">", "value": 0}}\n'
        '      ]}\n'
        '    }\n'
        '  },\n'
        '  "flags": ["<flag>"],\n'
        '  "variables": [{"id": "<v>", "default": 0}]\n'
        '}\n'
        '// You write JSON, never Ren\'Py — the compiler renders it (escaping/layout handled).\n'
        '// location = a background asset id from asset_manifest.backgrounds; it sets the scene\n'
        '//   image and every character who speaks in the node is shown over it. Tag EVERY node.\n'
        '// speaker = an EXACT premise.characters id, or null for narration (no "narrator").\n'
        '// end.type is one of: jump {target}, menu {choices:[{text,target,requires?,effects?}]},\n'
        '//   return (back to caller), end {ending?} (a definitive ending).\n'
        '// Every jump/menu target MUST be a node you also create, AND every node must be\n'
        '//   reachable: some node jumps/menus to it. Each premise.endings id is its own node.\n'
        '// effects (on a line / choice): set_flag, clear_flag, add_item, remove_item,\n'
        '//   set_var{var,value}, add_var{var,delta}. Declare flags/variables here.'
    ),
    "places": (
        '{\n'
        '  "start_place": "room_<first>",\n'
        '  "items": [ {"id": "item_<thing>", "name": "<Display Name>", "examine": "..."} ],\n'
        '  "flags": ["<flag_set_by_a_puzzle>"],\n'
        '  "goal": {"type": "flag", "id": "<flag_that_means_you_won>"},\n'
        '  "place_ids": ["room_<first>", "room_<second>"],\n'
        '  "places": {\n'
        '    "room_<first>": {\n'
        '      "kind": "room",\n'
        '      "background": "bg_<place>",\n'
        '      "interactables": [\n'
        '        {"id": "h_<thing>", "label": "<short noun>",\n'
        '         "position": {"rect": {"x": 340, "y": 210, "w": 180, "h": 160}},\n'
        '         "action": {"type": "examine", "text": "It is a heavy locked door."}}\n'
        '      ]\n'
        '    }\n'
        '  }\n'
        '}\n'
        '// A PLACE is a screen the player clicks. position.rect is pixels on a 1280x720 frame.\n'
        '// action.type is one of (you write the structured action, not Ren\'Py):\n'
        '//   examine {text}; take {item, text?}; talk {node}; move {target, requires?};\n'
        '//   use {clauses:[{requires, outcome:{text?,effects?}}], fallback?}; win {requires?}.\n'
        '// background must exist in asset_manifest.backgrounds; item ids must be declared in\n'
        '//   items; a talk node must exist in `nodes`; move/win targets resolve to places/win.\n'
        '// goal flag must be set by a reachable use-outcome, and some hotspot must have a win action.'
    ),
}


def skeleton_guide(component_ids=None, genre: str = "vn") -> str:
    ids = component_ids or GENRE_COMPONENTS.get(genre, GENRE_COMPONENTS["vn"])
    blocks = [f"### {cid}\n{SKELETONS[cid]}" for cid in ids if cid in SKELETONS]
    return "Required component shapes (author content in exactly these shapes):\n\n" + "\n\n".join(blocks)
