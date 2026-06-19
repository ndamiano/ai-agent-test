"""Structural schemas for the Ren'Py buildable components.

The agent authors component content itself, so it needs to know the exact shape
build()/compile_renpy consume — and writing the wrong shape must become an immediate,
precise steering signal rather than a crash 60 steps later. SCHEMAS validates a
component on write; SKELETONS shows the agent the shape to author.

These are Ren'Py-specific and injected into maestro.build_tools, so maestro itself
stays genre-agnostic. Validation is structural only (fields build needs) — Ren'Py
syntax is checked downstream by _validate_and_repair + lint.
"""

from typing import Callable, Dict, Optional


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
                return f"asset_manifest.items[{i}] needs an 'id' matching a rooms item id"
    if "title_card" in c and not isinstance(c["title_card"], dict):
        return "asset_manifest.title_card must be an object"
    return None


def _v_rooms(c: Dict) -> Optional[str]:
    room_ids = c.get("room_ids")
    rooms = c.get("rooms")
    if not isinstance(room_ids, list) or not room_ids:
        return "rooms.room_ids must be a non-empty list of room id strings"
    if not isinstance(rooms, dict):
        return "rooms.rooms must be an object mapping room_id -> {bg, hotspots}"
    if c.get("start_room") and c["start_room"] not in room_ids:
        return f"rooms.start_room {c['start_room']!r} is not in room_ids"
    if not isinstance(c.get("items", []), list):
        return "rooms.items must be a list (use [] if none)"
    for i, it in enumerate(c.get("items", [])):
        if not isinstance(it, dict) or not it.get("id"):
            return f"rooms.items[{i}] needs an 'id' (e.g. 'item_key')"
    goal = c.get("goal")
    if goal is not None and (not isinstance(goal, dict) or not goal.get("type") or not goal.get("id")):
        return "rooms.goal must be an object with 'type' ('flag' or 'room') and 'id'"
    # Validate only the rooms actually authored so far — count/each checks drive the rest.
    for rid, room in rooms.items():
        if not isinstance(room, dict):
            return f"rooms.rooms[{rid!r}] must be an object"
        if not room.get("bg"):
            return f"rooms.rooms[{rid!r}] needs a 'bg' (an asset_manifest background id)"
        hs = room.get("hotspots")
        if not isinstance(hs, list) or not hs:
            return f"rooms.rooms[{rid!r}].hotspots must be a non-empty list"
        for j, h in enumerate(hs):
            if not isinstance(h, dict):
                return f"rooms.rooms[{rid!r}].hotspots[{j}] must be an object"
            for field in ("id", "label", "logic"):
                if not h.get(field):
                    return f"rooms.rooms[{rid!r}].hotspots[{j}] needs a '{field}'"
            rect = h.get("rect")
            if not (isinstance(rect, list) and len(rect) == 4 and all(isinstance(n, (int, float)) for n in rect)):
                return f"rooms.rooms[{rid!r}].hotspots[{j}].rect must be [x, y, w, h] (4 numbers)"
    return None


def _v_node_scripts(c: Dict) -> Optional[str]:
    node_ids = c.get("node_ids")
    scripts = c.get("scripts")
    if not isinstance(node_ids, list) or not node_ids:
        return "node_scripts.node_ids must be a non-empty list of node id strings"
    if not isinstance(scripts, dict):
        return "node_scripts.scripts must be an object mapping node_id -> Ren'Py script text"
    if "start" in node_ids:
        return ("do not use 'start' as a node id — the builder adds 'label start' that "
                "jumps to node_ids[0]")
    for nid in node_ids:
        if nid not in scripts:
            return f"node_scripts.scripts is missing an entry for node id '{nid}'"
        if not isinstance(scripts[nid], str) or not scripts[nid].strip():
            return f"node_scripts.scripts['{nid}'] must be non-empty Ren'Py script text"
    return None


SCHEMAS: Dict[str, Callable[[Dict], Optional[str]]] = {
    "premise": _v_premise,
    "asset_manifest": _v_asset_manifest,
    "node_scripts": _v_node_scripts,
    "rooms": _v_rooms,
}

# Which components each genre's spec carries — so skeleton_guide shows the agent the
# right shapes. vn = visual novel (dialogue branches); point_and_click = rooms +
# hotspots + inventory, reusing node_scripts for NPC dialogue.
GENRE_COMPONENTS: Dict[str, list] = {
    "vn": ["premise", "asset_manifest", "node_scripts"],
    "point_and_click": ["premise", "asset_manifest", "rooms", "node_scripts"],
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
        '// drive: legible beats exotic ("save as many as I can" > "decide who dies but never\n'
        '//   be decided about"). The story supplies obstacles; drive is the surface they push on.\n'
        '// history: 2-3 DISTINCT whole events, each adding new info — not a role descriptor\n'
        '//   ("a medic in the war" is a competency), not one event split in two.\n'
        '// example_lines: must sound DIFFERENT between characters — swap-test them.'
    ),
    "asset_manifest": (
        '{\n'
        '  "backgrounds": [ {"id": "bg_<place>", "image_file": "<place>.png", "description": "..."} ],\n'
        '  "characters":  [ {"id": "<same id as premise>", "image_file": "<id>.png", "description": "..."} ],\n'
        '  "items": [ {"id": "<same id as a rooms item>", "image_file": "<id>.png", "description": "..."} ],\n'
        '  "cgs": [],\n'
        '  "title_card": {"image_file": "title_card.png", "description": "..."}\n'
        '}\n'
        '// asset_manifest holds IMAGES, not speakers. character ids here MUST match\n'
        '//   premise.characters ids exactly — this does not define a character, premise does.\n'
        '// items hold inventory ICONS; their ids MUST match rooms.items ids. Omit "items"\n'
        '//   (or use []) for a visual novel with no inventory.'
    ),
    "rooms": (
        '{\n'
        '  "start_room": "room_<first>",\n'
        '  "items": [ {"id": "item_<thing>", "name": "<Display Name>", "examine": "..."} ],\n'
        '  "flags": ["<flag_set_by_a_puzzle>"],          // booleans gating progress\n'
        '  "goal": {"type": "flag", "id": "<flag_that_means_you_won>"},\n'
        '  "room_ids": ["room_<first>", "room_<second>"],\n'
        '  "rooms": {\n'
        '    "room_<first>": {\n'
        '      "bg": "bg_<place>",\n'
        '      "hotspots": [\n'
        '        {"id": "h_<thing>", "rect": [340, 210, 180, 160], "label": "<short noun>",\n'
        '         "logic": "label hs_room_<first>_h_<thing>:\\n    \\"It is a heavy locked door.\\"\\n    return"}\n'
        '      ]\n'
        '    }\n'
        '  }\n'
        '}\n'
        '// A ROOM is a screen the player clicks around in. rect = [x, y, width, height] in\n'
        '//   pixels on a 1280x720 frame; keep boxes inside it and non-overlapping.\n'
        '// Each hotspot.logic is a Ren\'Py LABEL body named exactly hs_<room_id>_<hotspot_id>.\n'
        '//   It runs when clicked, then MUST end by `return` (back to the room) or `jump room_x`\n'
        '//   (move rooms). Verbs you compose in the logic body:\n'
        '//     examine: a bare "..." narration line, then return\n'
        '//     take:    $ if "item_x" not in inventory: inventory.append("item_x")  then return\n'
        '//     use:     if "item_x" in inventory:  then set a flag ($ flag = True) / jump, else "..."\n'
        '//     talk:    call <node_id>  (an NPC dialogue node you also create in node_scripts)\n'
        '//     move:    jump room_<other>\n'
        '//     win:     jump win   (reached once goal is satisfiable)\n'
        '// bg must exist in asset_manifest.backgrounds; every item id must appear in\n'
        '//   asset_manifest.items; the goal flag/room must be reachable through the clicks.'
    ),
    "node_scripts": (
        '{\n'
        '  "node_ids": ["scene_01", "scene_02", "ending_<slug>"],  // do NOT include "start"\n'
        '  "scripts": {\n'
        '    "scene_01": "label scene_01:\\n    scene bg_<place>\\n    show <char_a>\\n'
        '    <char_a> \\"...\\"\\n    show <char_b>\\n    <char_b> \\"...\\"\\n'
        '    <char_a> \\"...\\"\\n    menu:\\n        \\"choice one\\":\\n'
        '            jump scene_02\\n        \\"choice two\\":\\n            jump ending_<slug>"\n'
        '  }\n'
        '}\n'
        '// <char_a>/<char_b>/<place>/<slug> are placeholders — replace with the EXACT ids\n'
        '//   from premise/asset_manifest; never emit the angle-bracket tokens.\n'
        '// A node is a real SCENE, not one line: several dialogue beats between characters,\n'
        '//   then either a menu: with 2+ choices that jump to different nodes, or a jump.\n'
        '// scene <bg id> must exist in asset_manifest; show/speaker ids must exist in\n'
        '//   premise.characters (add the character to premise FIRST if it is new).\n'
        '// Every node_id needs a scripts entry; the builder adds "label start" -> node_ids[0].\n'
        '// Any jump/menu target MUST be a node you also create, AND every node you create\n'
        '//   must be reachable: some node must jump/menu to it (jump scene_02 -> build scene_02).\n'
        '// Each ending in premise.endings must be its own node, reached via choices.\n'
        '// Narration is a BARE quoted line with NO speaker: "    \\"...\\"" — no "narrator".'
    ),
}


def skeleton_guide(component_ids=None, genre: str = "vn") -> str:
    ids = component_ids or GENRE_COMPONENTS.get(genre, GENRE_COMPONENTS["vn"])
    blocks = [f"### {cid}\n{SKELETONS[cid]}" for cid in ids if cid in SKELETONS]
    return "Required component shapes (author content in exactly these shapes):\n\n" + "\n\n".join(blocks)
