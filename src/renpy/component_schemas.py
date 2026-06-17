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
    if "title_card" in c and not isinstance(c["title_card"], dict):
        return "asset_manifest.title_card must be an object"
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
        '    {"id": "<snake_case_id>", "name": "<Display Name>", "voice": "one-line voice", "color": "#c8ffc8"}\n'
        '  ],\n'
        '  "endings": [ {"id": "ending_<slug>", "description": "..."} ]\n'
        '}\n'
        '// Invent ids/names from THE REQUEST. The angle-bracket tokens are placeholders —\n'
        '//   never emit them literally, and do not reuse example ids from other prompts.'
    ),
    "asset_manifest": (
        '{\n'
        '  "backgrounds": [ {"id": "bg_<place>", "image_file": "<place>.png", "description": "..."} ],\n'
        '  "characters":  [ {"id": "<same id as premise>", "image_file": "<id>.png", "description": "..."} ],\n'
        '  "cgs": [],\n'
        '  "title_card": {"image_file": "title_card.png", "description": "..."}\n'
        '}\n'
        '// asset_manifest holds IMAGES, not speakers. character ids here MUST match\n'
        '//   premise.characters ids exactly — this does not define a character, premise does.'
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


def skeleton_guide(component_ids=None) -> str:
    ids = component_ids or list(SKELETONS)
    blocks = [f"### {cid}\n{SKELETONS[cid]}" for cid in ids if cid in SKELETONS]
    return "Required component shapes (author content in exactly these shapes):\n\n" + "\n\n".join(blocks)
