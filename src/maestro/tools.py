"""Artifact tools — the frequent, autonomous capabilities the executor dispatches.

Bound to one run's durable state. build_tools(spec, state) returns the {name: fn}
registry the Executor consumes; the agent chooses which to call and when.

The build tools (write_component, generate_asset) refuse to run until the spec is
frozen — the human gate is load-bearing. read_component, validate, compile_renpy
are safe before freezing.

State is bounded on purpose: there is no raw read_file/write_file. Components are
written by id.
"""

import json
from typing import Callable, Dict, List, Optional

from maestro.ir_assemble import EMOTIONS, is_narration_speaker


class SpecNotFrozen(RuntimeError):
    pass


# Story-state delta fields, so write_node can accept them whether nested under
# story_state_delta or passed flat (the model does both).
_DELTA_FIELDS = ("new_facts", "entity_updates", "open_threads_add",
                 "open_threads_resolve", "event_summary")

# The agent writes structured IR (JSON), never Ren'Py text — so the old text-repair
# machinery (over-escape collapsing, smart-punctuation folding, fuzzy snippet finding,
# speaker-name validation) is gone. Escaping is the compiler's job (ir_vn/ir_pnc via
# json.dumps); reference integrity (speakers, targets) is ir_crossref's, run at compile.

_EMOTIONS = set(EMOTIONS)


def _coerce_json(value):
    """A small model frequently passes a nested object (a node's content, a place body) as a
    JSON STRING — `content: "{\\"lines\\": ...}"` — instead of an object, and the write is then
    rejected for a wrong type, burning a step. Parse a JSON-looking string back to the object it
    encodes; leave anything else untouched (a real type error still surfaces below)."""
    if isinstance(value, str):
        s = value.strip()
        if s[:1] in ("{", "["):
            try:
                return json.loads(s)
            except ValueError:
                return value
    return value













def _cell_xy(pos) -> Optional[tuple]:
    """The (x, y) of a {cell:{x,y}} tile position, or None if it isn't an integer tile."""
    if isinstance(pos, dict) and isinstance(pos.get("cell"), dict):
        cell = pos["cell"]
        if isinstance(cell.get("x"), int) and isinstance(cell.get("y"), int):
            return (cell["x"], cell["y"])
    return None


def _place_content_error(content) -> Optional[str]:
    if not isinstance(content, dict):
        return "place content must be a JSON object {kind, background, interactables}"
    inter = content.get("interactables")
    if not isinstance(inter, list) or not inter:
        return "place.interactables must be a non-empty list of clickable objects"
    for j, h in enumerate(inter):
        if not isinstance(h, dict) or not h.get("id"):
            return f"place.interactables[{j}] needs an 'id'"
        from maestro.modules.world import action_error
        err = action_error(h.get("action"))
        if err:
            return f"place.interactables[{j}] ({h.get('id')}): {err}"
    return None


# OpenAI-format schemas for the decider's tool-calling. Kept beside build_tools so
# the names stay in sync.
TOOL_SCHEMAS: List[Dict] = [
    {"type": "function", "function": {
        "name": "write_component",
        "description": "Write (fill or overwrite) an artifact component by id. You author the content.",
        "parameters": {"type": "object", "properties": {
            "component_id": {"type": "string", "description": "Component id, e.g. 'characters'"},
            "content": {"type": "object", "description": "The component's full content as JSON"},
        }, "required": ["component_id", "content"]}}},
    {"type": "function", "function": {
        "name": "add_character",
        "description": "Author ONE character into `characters` (raises the cast count). Author the "
                       "people one at a time — each distinct from the ones already written.",
        "parameters": {"type": "object", "properties": {
            "character_id": {"type": "string", "description": "snake_case id, e.g. 'evelyn'"},
            "content": {"type": "object", "description":
                "{name, role: protagonist|antagonist|npc, voice, sex: male|female, temperament, "
                "drive, history:[...], competencies:[...], example_lines:[...], color}"},
        }, "required": ["character_id", "content"]}}},
    {"type": "function", "function": {
        "name": "set_spine",
        "description": "Set the story's SPINE — theme + tone (+ optional trope), the frame every "
                       "storyline serves. Author it first (replaces a central question).",
        "parameters": {"type": "object", "properties": {
            "theme": {"type": "string", "description": "what the story is about, e.g. 'loyalty tested by scarcity'"},
            "tone": {"type": "string", "description": "the tone, e.g. 'wry and warm, occasionally bleak'"},
            "trope": {"type": "string", "description": "OPTIONAL recognizable frame, e.g. 'heist', 'enemies to lovers'"},
        }, "required": ["theme", "tone"]}}},
    {"type": "function", "function": {
        "name": "add_storyline",
        "description": "Author ONE linear storyline's SHELL into `story` — its kind, premise, "
                       "terminus (how it ends), length, and any branch points. Its beats are filled "
                       "in later, one at a time. Exactly one storyline is kind='main'.",
        "parameters": {"type": "object", "properties": {
            "storyline_id": {"type": "string", "description": "e.g. 'sl_main', 'sl_cover_vale'"},
            "content": {"type": "object", "description":
                "{kind: main|side, premise, target_beats:int, branches:[{id, from_beat, choice, "
                "spinoff, return_to_beat(null=terminal), requires?}], terminus:{type: game_end|"
                "handoff|merge, ...} — game_end carries ending:{id, description}}"},
        }, "required": ["storyline_id", "content"]}}},
    {"type": "function", "function": {
        "name": "add_beat",
        "description": "Author ONE beat into a storyline (raises that line's beat count). Beats grow "
                       "one at a time, in dramatic order, each continuing that line's arc so far.",
        "parameters": {"type": "object", "properties": {
            "storyline_id": {"type": "string", "description": "the storyline this beat belongs to"},
            "beat_id": {"type": "string", "description": "e.g. 'beat_03' (numbered across the whole story)"},
            "content": {"type": "object", "description":
                "{summary: the scene author's whole brief, type: bonding|comedy|friction|plot|"
                "character, purpose: setup|inciting|escalation|midpoint_turn|crisis|climax|"
                "resolution, tension: the stakes dial ('none' is a real answer)}"},
        }, "required": ["storyline_id", "beat_id", "content"]}}},
    {"type": "function", "function": {
        "name": "finish_storyline",
        "description": "Declare a storyline complete at its natural length (>= the beat floor) — "
                       "stops its beat demand. Use when the line's arc is done, not padded to a target.",
        "parameters": {"type": "object", "properties": {
            "storyline_id": {"type": "string"},
        }, "required": ["storyline_id"]}}},
    {"type": "function", "function": {
        "name": "set_bible",
        "description": "Set the world BIBLE's setting + factions on `bible` — the world premise and "
                       "the groups its conflicts run between. Author it first (the world root the "
                       "places/residents/quests derive from). Merges: pass only what changes.",
        "parameters": {"type": "object", "properties": {
            "setting": {"type": "string", "description":
                "one vivid line: the place, its tech level, the pressure on it, e.g. "
                "'a drought-starved river barony, iron-age, superstitious'"},
            "factions": {"type": "array", "items": {"type": "object"}, "description":
                "[{id, name, wants}] — 2-4 groups whose wants collide"},
        }, "required": ["setting"]}}},
    {"type": "function", "function": {
        "name": "add_faction",
        "description": "Author ONE faction into `bible` — a named group with a want the world's "
                       "tensions pull on. Use to declare a faction a tension already references.",
        "parameters": {"type": "object", "properties": {
            "faction_id": {"type": "string", "description": "snake_case, e.g. 'fac_guild'"},
            "content": {"type": "object", "description":
                "{name, wants: what it is after, in one clause}"},
        }, "required": ["faction_id", "content"]}}},
    {"type": "function", "function": {
        "name": "add_tension",
        "description": "Author ONE tension into `bible` (raises the tension count) — a standing "
                       "conflict pulling the world, between declared factions. Exactly one tension "
                       "is scale='main' (the world's central conflict). Grow them one at a time.",
        "parameters": {"type": "object", "properties": {
            "tension_id": {"type": "string", "description": "e.g. 'tension_levy'"},
            "content": {"type": "object", "description":
                "{summary: the concrete conflict, scale: main|side, between: [faction ids this "
                "conflict runs between]}"},
        }, "required": ["tension_id", "content"]}}},
    {"type": "function", "function": {
        "name": "add_item",
        "description": "Declare ONE item into `items` — a thing the player holds. Author it where a "
                       "reference already demands it (a take hotspot, a use/requires gate, an "
                       "add_item effect); items are created where used, not guessed up front.",
        "parameters": {"type": "object", "properties": {
            "item_id": {"type": "string", "description":
                "snake_case, prefixed item_, e.g. 'item_key' — the EXACT referenced id"},
            "content": {"type": "object", "description": "{name, examine: what it IS and DOES}"},
        }, "required": ["item_id", "content"]}}},
    {"type": "function", "function": {
        "name": "write_node",
        "description": "Write one dialogue node into `nodes` AND merge its story-state delta in "
                       "the same call. The node is a JSON object: ordered `lines` plus a terminal "
                       "`end`. No Ren'Py — the compiler renders it.",
        "parameters": {"type": "object", "properties": {
            "node_id": {"type": "string"},
            "content": {"type": "object", "description":
                "{lines: [{speaker: <char id or null for narration>, text, effects?:[...]}], "
                "end: {type: 'jump'|'menu'|'return'|'end', ...}}"},
            "story_state_delta": {"type": "object", "description":
                "new_facts[], entity_updates{}, open_threads_add[], "
                "open_threads_resolve[], event_summary"},
        }, "required": ["node_id", "content"]}}},
    {"type": "function", "function": {
        "name": "write_scene",
        "description": "Write one dialogue scene as SCREENPLAY TEXT (not JSON lines) plus a "
                       "structured `end`, and merge its story-state delta in the same call. "
                       "Each script line is `NAME: text` or `NAME [emotion]: text`; use NARR for "
                       "narration.",
        "parameters": {"type": "object", "properties": {
            "node_id": {"type": "string"},
            "script": {"type": "string", "description":
                "the whole scene as screenplay text, one line per line, e.g.\n"
                "NARR: The hallway light flickers.\n"
                "MARA [worried]: You left the door open.\n"
                "JONAS: I left it open for you."},
            "end": {"type": "object", "description":
                "{type: 'jump', target} | {type: 'menu', choices: [{text, target}]} | "
                "{type: 'end'}"},
            "location": {"type": "string", "description":
                "background id from asset_manifest for this scene"},
            "story_state_delta": {"type": "object", "description":
                "new_facts[], entity_updates{}, open_threads_add[], "
                "open_threads_resolve[], event_summary"},
        }, "required": ["node_id", "script", "end"]}}},
    {"type": "function", "function": {
        "name": "edit_node",
        "description": "Patch ONE field of an existing node without rewriting it: replace a "
                       "single line by index (text/speaker/emotion/effects) or replace the node's "
                       "`end`. Use to repoint a jump/menu target or fix one line.",
        "parameters": {"type": "object", "properties": {
            "node_id": {"type": "string"},
            "line_index": {"type": "integer", "description": "index into lines to patch (0-based)"},
            "text": {"type": "string", "description": "new text for that line"},
            "speaker": {"type": ["string", "null"], "description": "a plain cast character id "
                        "STRING, or null for narration — never an object or list"},
            "emotion": {"type": "string", "description": "new speaker expression: one of "
                        "neutral, happy, sad, angry, surprised, worried"},
            "effects": {"type": "array", "items": {"type": "object"},
                        "description": "replace that line's effects"},
            "end": {"type": "object", "description": "replace the node's terminal end object"},
            "location": {"type": "string", "description": "set the node's background (a "
                         "background id from asset_manifest, e.g. 'bg_office')"},
        }, "required": ["node_id"]}}},
    {"type": "function", "function": {
        "name": "write_place",
        "description": "Write one place into `places`: its interactables (each a position + a "
                       "structured `action`), plus a backdrop (room) or a layout PLAN (walkable). "
                       "Adds it to place_ids. A point-and-click room (kind 'room') has pixel-rect "
                       "hotspots + background. A walkable RPG zone (kind 'world_map'/'town'/"
                       "'interior') is authored as a `layout` — features on a coarse region grid; "
                       "a deterministic builder places every tile. NEVER write tiles/rows/cells.",
        "parameters": {"type": "object", "properties": {
            "place_id": {"type": "string", "description": "e.g. 'room_kitchen' or 'zone_crypt'"},
            "content": {"type": "object", "description":
                "{kind: 'room' (point-and-click) | 'world_map'|'town'|'interior' (walkable RPG), "
                "interactables: [{id, label, position, action:{type, ...}}], "
                "background: <asset id> (ROOM only), "
                "layout: {size:'small'|'medium'|'large', terrain:{open, blocked}, features:[{id, "
                "kind, at:<region>, theme?, label?}], exits:[{id, edge}], connections:[{from, to}]} "
                "(RPG only). A position is {rect:{x,y,w,h}} for a room or {feature:'<layout id>'} "
                "for an RPG zone."},
        }, "required": ["place_id", "content"]}}},
    {"type": "function", "function": {
        "name": "edit_place",
        "description": "Patch ONE interactable in a place without rewriting it: replace its "
                       "`action` (e.g. repoint a move target / fix a use clause), `position`, or "
                       "`label`.",
        "parameters": {"type": "object", "properties": {
            "place_id": {"type": "string"},
            "interactable_id": {"type": "string"},
            "action": {"type": "object", "description": "replacement structured action"},
            "position": {"type": "object", "description":
                         "replacement {rect:{x,y,w,h}} (room) or {cell:{x,y}} (RPG tile)"},
            "label": {"type": "string"},
        }, "required": ["place_id", "interactable_id"]}}},
    {"type": "function", "function": {
        "name": "add_interactable",
        "description": "APPEND one new interactable to an existing place — without rewriting it "
                       "(write_place clobbers the others) and without repointing an existing one "
                       "(edit_place breaks that hotspot's route). The right tool to add a move "
                       "hotspot for an unreachable place, a use/win hotspot to set the goal flag, or "
                       "a start_combat tile to enter a fight.",
        "parameters": {"type": "object", "properties": {
            "place_id": {"type": "string", "description": "the existing place to add to"},
            "interactable": {"type": "object", "description":
                "{id, label, position, action:{type, ...}} — a new interactable; position is "
                "{rect:{x,y,w,h}} (room) or {cell:{x,y}} (RPG tile); its id must not already exist"},
        }, "required": ["place_id", "interactable"]}}},
    {"type": "function", "function": {
        "name": "read_place",
        "description": "Read one place's current background + interactables (with their actions).",
        "parameters": {"type": "object", "properties": {
            "place_id": {"type": "string"}}, "required": ["place_id"]}}},
    {"type": "function", "function": {
        "name": "set_places_meta",
        "description": "Declare the game's global scaffold on `places`: the optional win `goal`, "
                       "puzzle `flags`, numeric `variables`, `start_place`, and (walkable RPG only) "
                       "`start_spawn` — the player's start TILE. Merges (pass only what changes). "
                       "Items live in the `items` catalogue, not here.",
        "parameters": {"type": "object", "properties": {
            "goal": {"type": "object", "description":
                     "{type: 'flag'|'room', id: '<winning flag or place id>'}"},
            "flags": {"type": "array", "items": {"type": "string"},
                      "description": "puzzle boolean names, e.g. ['door_open', 'escaped']"},
            "variables": {"type": "array", "items": {"type": "object"},
                          "description": "[{id, default}] numeric state"},
            "start_place": {"type": "string"},
            "start_spawn": {"type": "object", "description":
                            "RPG only: the player's start tile, {cell:{x,y}} in start_place"},
        }, "required": []}}},
    {"type": "function", "function": {
        "name": "set_combat_meta",
        "description": "Lay the combat foundation on `combat`: combat_model + the stat SYSTEM (you "
                       "need an hp-like resource_depletable stat so a fight can end) + optional "
                       "statuses. Call ONCE before authoring abilities. Combat games only.",
        "parameters": {"type": "object", "properties": {
            "combat_model": {"type": "string", "description": "'turn_based' (plays today)"},
            "stats": {"type": "array", "items": {"type": "object"}, "description":
                "[{id, default, role, min?, max?}] — role: resource_depletable | "
                "resource_regenerating | modifier | rating"},
            "statuses": {"type": "array", "items": {"type": "object"}, "description":
                "optional [{id, name, tick?:[{stat,op,formula}], blocks_action?}]"},
        }, "required": ["stats"]}}},
    {"type": "function", "function": {
        "name": "write_ability",
        "description": "Author ONE combat ability into `combat` (raises the ability count). Combat "
                       "games only; stats/statuses must already be declared via set_combat_meta.",
        "parameters": {"type": "object", "properties": {
            "ability_id": {"type": "string", "description": "e.g. 'firebolt'"},
            "content": {"type": "object", "description":
                "{name, targeting:{shape,faction,range?}, effects:[{stat,op,formula} | "
                "{status,duration} | {world:{...}}], cost?:[{stat,amount}], requires?:<condition>}"},
        }, "required": ["ability_id", "content"]}}},
    {"type": "function", "function": {
        "name": "write_combatant",
        "description": "Author ONE combatant into `combat` (raises the combatant count). Its "
                       "abilities must already exist (write_ability). Combat games only.",
        "parameters": {"type": "object", "properties": {
            "combatant_id": {"type": "string", "description": "e.g. 'cb_hero'"},
            "content": {"type": "object", "description":
                "{character:<a cast id>, stats:[{stat,value}], abilities:[<ability ids>]}"},
        }, "required": ["combatant_id", "content"]}}},
    {"type": "function", "function": {
        "name": "write_encounter",
        "description": "Author ONE encounter into `combat` (raises the encounter count). Its "
                       "combatants must already exist (write_combatant). Combat games only.",
        "parameters": {"type": "object", "properties": {
            "encounter_id": {"type": "string", "description": "e.g. 'enc_crypt'"},
            "content": {"type": "object", "description":
                "{background?, combatants:[{ref:<combatant id>, faction:player|enemy|ally|neutral, "
                "position?}], victory:{all_defeated:<faction>} | {when:<cond>}, defeat?, "
                "on_victory?:<node_end>, on_defeat?:<node_end>}"},
        }, "required": ["encounter_id", "content"]}}},
    {"type": "function", "function": {
        "name": "set_progression",
        "description": "Declare the growth loop on `combat`: the player's canonical combatant, "
                       "the XP curve, and per-level stat growth. Combat games only.",
        "parameters": {"type": "object", "properties": {
            "progression": {"type": "object", "description":
                "{player:<combatant id — the PROTAGONIST's>, xp_var?:'xp', level_var?:'level', "
                "per_level?:<int, default 20>, growth:[{stat:<declared stat>, per_level:<n>}]} — "
                "declares the xp/level variables for you; victories and any other effect can "
                "then add_var the xp pool, and conditions can gate on the level"},
        }, "required": ["progression"]}}},
    {"type": "function", "function": {
        "name": "set_encounter_table",
        "description": "Give ONE walkable zone wild fights: each step on open ground rolls "
                       "`rate`; a hit fights one combatant drawn from entries by weight, stats "
                       "scaled by tier. Combat games only.",
        "parameters": {"type": "object", "properties": {
            "place_id": {"type": "string", "description": "a dangerous walkable zone"},
            "table": {"type": "object", "description":
                "{rate:<0-1 per-step chance>, entries:[{combatant:<id>, weight?:<int>, "
                "tier?:<stat multiplier, 1.0 base>}]}"},
        }, "required": ["place_id", "table"]}}},
    {"type": "function", "function": {
        "name": "read_component",
        "description": "Read a component you previously wrote.",
        "parameters": {"type": "object", "properties": {
            "component_id": {"type": "string"}}, "required": ["component_id"]}}},
    {"type": "function", "function": {
        "name": "read_node",
        "description": "Read one node's current content (lines + end) — to see its lines and "
                       "targets before editing it.",
        "parameters": {"type": "object", "properties": {
            "node_id": {"type": "string"}}, "required": ["node_id"]}}},
    {"type": "function", "function": {
        "name": "read_story_state",
        "description": "Read the continuity bible (facts, entity states, open threads, "
                       "recent events) before writing the next node.",
        "parameters": {"type": "object", "properties": {}, "required": []}}},
    {"type": "function", "function": {
        "name": "generate_asset",
        "description": "Generate the image assets the asset_manifest declares.",
        "parameters": {"type": "object", "properties": {}, "required": []}}},
    {"type": "function", "function": {
        "name": "validate",
        "description": "Recompute the to-do: which done-conditions still fail.",
        "parameters": {"type": "object", "properties": {
            "component_id": {"type": "string", "description": "Optional: scope to one component"}},
            "required": []}}},
    {"type": "function", "function": {
        "name": "request_review",
        "description": "Ask the human to choose, when genuinely stuck between options.",
        "parameters": {"type": "object", "properties": {
            "question": {"type": "string"},
            "options": {"type": "array", "items": {"type": "string"}},
        }, "required": ["question"]}}},
]

def build_tools(spec, state, modules=None) -> Dict[str, Callable]:
    """Bind the artifact tools to one run's state. `spec` is the spec dict (or a Spec); `modules` is
    the composed module list — derived from `spec["modules"]` when omitted. Structural write-time
    validators + the lock discipline are read from the modules."""
    from maestro.modules.context import build_context
    from maestro.modules import compose
    from maestro.modules.combat import (combat_meta_error, ability_write_error,
                                        combatant_write_error, encounter_write_error)

    spec = getattr(spec, "data", spec)
    if modules is None:
        modules = compose(spec.get("modules", []))
    schemas = {cid: v for m in modules for cid, v in m.schemas.items()}
    params = spec.get("params") or {}

    # Born-compliant nodes: write_node enforces each_node_min_lines at creation, so a thin node is
    # rejected up front instead of passing as a stub and dragging the model through a repair phase.
    _node_min_lines = params.get("each_node_min_lines", 0)

    def _known_bgs() -> List[str]:
        man = state.read_component("asset_manifest") or {}
        return [b["id"] for b in (man.get("backgrounds") or [])
                if isinstance(b, dict) and b.get("id")]

    # A component LOCKS once no module reports an error ON it: a full rewrite would otherwise drop
    # ids other components already reference. The sweep covers every module that AFFECTS the
    # component (owner + cross-cutting like `state` — a state_wiring error saying "cut the
    # declaration" on `items` must keep `items` writable) and open human todos targeting it.
    # EXCEPTION: the compile terminal (emits_compile) stays writable until the build ends (its
    # correctness depends on the whole artifact, and we never run its expensive compile just to
    # test a lock).
    _owner = {cid: m for m in modules for cid in m.affected_components()}
    _terminal = {getattr(m, "component", None) for m in modules if getattr(m, "emits_compile", False)}
    # In a scenes-less world game, talk hotspots demand nodes on demand — `nodes` must stay
    # writable or the second talk target can never be authored (world's crossref fix needs to
    # ADD a node, and no composed module would unlock it).
    if any(m.id == "world" for m in modules):
        _terminal.add("nodes")

    def _require_frozen():
        if not spec.get("frozen"):
            raise SpecNotFrozen("spec must be frozen before building the artifact")

    def _locked(component_id: str) -> bool:
        from maestro.modules import human as human_mod
        if _owner.get(component_id) is None or component_id in _terminal:
            return False
        if state.read_component(component_id) is None:
            return False
        if any((t.get("component_id") or "") == component_id for t in human_mod.open_todos(state)):
            return False
        # A dirty asset is a human directive to rewrite that component — keep it writable so the
        # loop's dirty fix can land the edit (mirrors the open-todo exception above).
        if any(human_mod.split_idkey(d.get("idkey", ""))[0] == component_id
               for d in human_mod.dirty_entries(state)):
            return False
        ctx = build_context(spec, state)
        return not any(e.component == component_id
                       for m in modules if component_id in m.affected_components()
                       for e in m.get_errors(ctx))

    def _locked_error(component_id: str) -> Dict:
        return {"ok": False, "error":
                f"{component_id} is complete and locked — all its done-conditions pass. "
                f"Do NOT rewrite it. The remaining failures are in OTHER components; fix "
                f"those to conform to {component_id} (use its existing ids; read_component "
                f"to see them)."}

    def _schema_error(component_id: str, content):
        validator = schemas.get(component_id)
        if not validator:
            return None
        if not isinstance(content, dict):
            return f"{component_id} must be a JSON object, not a {type(content).__name__}"
        return validator(content)

    # ── artifact mutation (gated on freeze) ──────────────────────────────────
    def write_component(component_id: str, content, force: bool = False) -> Dict:
        # force: a human edit may overwrite a locked component (explicit override). The agent
        # never sets it — force isn't in TOOL_SCHEMAS — so its lock discipline is unchanged.
        _require_frozen()
        if _owner and not force and component_id not in _owner:
            # An unknown id (a model writing module-name "inventory" for the `items` component)
            # must fail loudly — an "ok" for a junk file is a false success signal it loops on.
            # force (human editor) and a module-less spec stay unconstrained.
            return {"ok": False, "error":
                    f"no component {component_id!r} — the components of this game are "
                    f"{sorted(_owner)}. Write the one your target names."}
        if not force and _locked(component_id):
            return _locked_error(component_id)
        content = _coerce_json(content)
        # Reject the wrong shape up front so it's an immediate steering signal, not a
        # crash inside compile later. The bad content is NOT persisted.
        err = _schema_error(component_id, content)
        if err:
            return {"ok": False, "error": f"invalid {component_id}: {err}"}
        if component_id == "nodes" and isinstance(content, dict):
            # A whole-component rewrite (state/human fixes) must obey the same per-node policy
            # write_node enforces, and must not orphan the system-owned beat/synopsis metadata.
            from maestro.modules.scenes import end_error
            prior = state.read_component("nodes") or {}
            for nid, node in (content.get("nodes") or {}).items():
                if not isinstance(node, dict):
                    continue
                e = end_error(node.get("end"))
                if e:
                    return {"ok": False, "error": f"invalid nodes.nodes[{nid!r}]: {e}"}
                old = (prior.get("nodes") or {}).get(nid)
                if isinstance(old, dict) and "beat" not in node and "beat" in old:
                    node["beat"] = old["beat"]
                if isinstance(old, dict) and "storyline" not in node and "storyline" in old:
                    node["storyline"] = old["storyline"]
            if "synopses" not in content and prior.get("synopses"):
                content["synopses"] = prior["synopses"]
        state.write_component(component_id, content)
        return {"ok": True, "component_id": component_id}

    def add_character(character_id: str, content) -> Dict:
        """Author ONE character into `characters` (append-by-id, no overwrite) — mirrors the combat
        write_* slices. The cast is grown one person at a time so each is authored with the others
        already in context."""
        _require_frozen()
        if _locked("characters"):
            return _locked_error("characters")
        content = _coerce_json(content)
        if not isinstance(content, dict):
            return {"ok": False, "error": "content must be a JSON object (the character's fields)"}
        from maestro.modules.cast import v_character_one
        chars = state.read_component("characters") or {"characters": []}
        if any(isinstance(c, dict) and c.get("id") == character_id
               for c in chars.get("characters", [])):
            return {"ok": False, "error": f"character {character_id!r} already exists — to raise the "
                    f"cast count write a NEW character id, do not rewrite one."}
        full = {**content, "id": character_id}
        err = v_character_one(full)
        if err:
            return {"ok": False, "error": err}
        chars.setdefault("characters", []).append(full)
        state.write_component("characters", chars)
        return {"ok": True, "character_id": character_id}

    # ── story: the dramatic plan, authored piece by piece (spine, then storylines, then
    #    beats/branches). Each writes a slice of the `story` component (like combat's write_* slices).
    def _story_doc() -> Dict:
        story = state.read_component("story") or {}
        story.setdefault("storylines", [])
        return story

    def _find_storyline(story, sid):
        for s in story["storylines"]:
            if isinstance(s, dict) and s.get("id") == sid:
                return s
        return None

    def set_spine(theme: str, tone: str, trope: str = None) -> Dict:
        """Set the story's SPINE — theme + tone (+ optional trope), the frame every storyline serves."""
        _require_frozen()
        if _locked("story"):
            return _locked_error("story")
        if not (isinstance(theme, str) and theme.strip()) or not (isinstance(tone, str) and tone.strip()):
            return {"ok": False, "error": "set_spine needs a non-empty theme and tone (the story's frame)"}
        story = _story_doc()
        story["spine"] = {"theme": theme.strip(), "tone": tone.strip(),
                          "trope": trope.strip() if isinstance(trope, str) and trope.strip() else None}
        state.write_component("story", story)
        return {"ok": True}

    def add_storyline(storyline_id: str, content) -> Dict:
        """Author ONE linear storyline's SHELL — kind, premise, terminus, target_beats, and any
        branch points. Its beats are filled in later, one at a time."""
        _require_frozen()
        if _locked("story"):
            return _locked_error("story")
        content = _coerce_json(content)
        if not isinstance(content, dict):
            return {"ok": False, "error": "content must be a JSON object (the storyline shell)"}
        from maestro.modules.story import v_spinoff_terminus, v_storyline
        story = _story_doc()
        if _find_storyline(story, storyline_id):
            return {"ok": False, "error": f"storyline {storyline_id!r} already exists — write a NEW id."}
        full = {**content, "id": storyline_id, "beats": content.get("beats") or [],
                "branches": content.get("branches") or []}
        err = v_storyline(full, require_beats=False) or v_spinoff_terminus(story, full)
        if err:
            return {"ok": False, "error": err}
        story["storylines"].append(full)
        if full.get("kind") == "main":
            story["start_storyline"] = storyline_id
        state.write_component("story", story)
        return {"ok": True, "storyline_id": storyline_id}

    def add_beat(storyline_id: str, beat_id: str, content) -> Dict:
        """Author ONE beat into a storyline (append-by-id within that line, no overwrite) — the line
        grows one beat at a time, in dramatic order."""
        _require_frozen()
        if _locked("story"):
            return _locked_error("story")
        content = _coerce_json(content)
        if not isinstance(content, dict):
            return {"ok": False, "error": "content must be a JSON object (the beat's fields)"}
        from maestro.modules.story import v_beat_one
        story = _story_doc()
        sl = _find_storyline(story, storyline_id)
        if sl is None:
            return {"ok": False, "error": f"storyline {storyline_id!r} does not exist — add_storyline it first."}
        sl.setdefault("beats", [])
        if any(isinstance(b, dict) and b.get("id") == beat_id for b in sl["beats"]):
            return {"ok": False, "error": f"beat {beat_id!r} already exists in {storyline_id!r} — write a NEW beat id."}
        full = {**content, "id": beat_id}
        err = v_beat_one(full)
        if err:
            return {"ok": False, "error": err}
        sl["beats"].append(full)
        state.write_component("story", story)
        return {"ok": True, "beat_id": beat_id}

    def finish_storyline(storyline_id: str) -> Dict:
        """Declare a storyline complete at its natural length (>= the beat floor) — stops its beat demand."""
        _require_frozen()
        if _locked("story"):
            return _locked_error("story")
        story = _story_doc()
        sl = _find_storyline(story, storyline_id)
        if sl is None:
            return {"ok": False, "error": f"storyline {storyline_id!r} does not exist."}
        sl["done"] = True
        state.write_component("story", story)
        return {"ok": True, "storyline_id": storyline_id}

    # ── bible: the world root, authored piece by piece (setting+factions, then tensions one at a
    #    time). Each writes a slice of the `bible` component (like story's slices). Authoring-only —
    #    the bible is never lifted into the IR.
    def set_bible(setting, factions=None) -> Dict:
        """Set the world's setting + (optionally) its faction roster. Merges by faction id."""
        _require_frozen()
        if _locked("bible"):
            return _locked_error("bible")
        if not (isinstance(setting, str) and setting.strip()):
            return {"ok": False, "error": "set_bible needs a non-empty setting (the world premise)"}
        from maestro.modules.bible import v_faction
        bible = state.read_component("bible") or {}
        bible["setting"] = setting.strip()
        by_id = {f["id"]: f for f in bible.get("factions") or []
                 if isinstance(f, dict) and f.get("id")}
        if factions is not None:
            factions = _coerce_json(factions)
            if not isinstance(factions, list):
                return {"ok": False, "error": "factions must be a list of {id, name, wants}"}
            for f in factions:
                err = v_faction(f)
                if err:
                    return {"ok": False, "error": err}
                by_id[f["id"]] = f
        bible["factions"] = list(by_id.values())
        bible.setdefault("tensions", [])
        state.write_component("bible", bible)
        return {"ok": True, "factions": [f["id"] for f in bible["factions"]]}

    def add_faction(faction_id: str, content) -> Dict:
        """Author ONE faction (append-by-id, no overwrite)."""
        _require_frozen()
        if _locked("bible"):
            return _locked_error("bible")
        content = _coerce_json(content)
        if not isinstance(content, dict):
            return {"ok": False, "error": "content must be a JSON object {name, wants}"}
        from maestro.modules.bible import v_faction
        bible = state.read_component("bible") or {}
        factions = bible.setdefault("factions", [])
        if any(isinstance(f, dict) and f.get("id") == faction_id for f in factions):
            return {"ok": False, "error": f"faction {faction_id!r} already exists — write a NEW id."}
        full = {**content, "id": faction_id}
        err = v_faction(full)
        if err:
            return {"ok": False, "error": err}
        factions.append(full)
        bible.setdefault("tensions", [])
        state.write_component("bible", bible)
        return {"ok": True, "faction_id": faction_id}

    def add_tension(tension_id: str, content) -> Dict:
        """Author ONE tension (append-by-id, no overwrite). Rejects a SECOND scale='main' — exactly
        one tension is the world's central conflict."""
        _require_frozen()
        if _locked("bible"):
            return _locked_error("bible")
        content = _coerce_json(content)
        if not isinstance(content, dict):
            return {"ok": False, "error": "content must be a JSON object (the tension's fields)"}
        from maestro.modules.bible import v_tension
        bible = state.read_component("bible") or {}
        tensions = bible.setdefault("tensions", [])
        if any(isinstance(t, dict) and t.get("id") == tension_id for t in tensions):
            return {"ok": False, "error": f"tension {tension_id!r} already exists — write a NEW id."}
        full = {**content, "id": tension_id}
        err = v_tension(full)
        if err:
            return {"ok": False, "error": err}
        if full.get("scale") == "main" and any(
                isinstance(t, dict) and t.get("scale") == "main" for t in tensions):
            return {"ok": False, "error": "a main tension already exists — this one must be "
                    "scale='side' (exactly one tension is the world's central conflict)."}
        tensions.append(full)
        bible.setdefault("factions", [])
        state.write_component("bible", bible)
        return {"ok": True, "tension_id": tension_id}

    def add_item(item_id: str, content) -> Dict:
        """Declare ONE item (append-by-id, no overwrite) — but ONLY where a reference already demands
        it. Items are born where used: a hotspot/effect/gate names the id, then this fills it in, so
        the catalogue is exactly what the game uses (never a floating list `state` must then police)."""
        _require_frozen()
        if _locked("items"):
            return _locked_error("items")
        content = _coerce_json(content)
        if not isinstance(content, dict):
            return {"ok": False, "error": "content must be a JSON object (name + examine)"}
        from maestro.modules.inventory import v_item_one, demanded_items
        items = state.read_component("items") or {"items": []}
        if any(isinstance(i, dict) and i.get("id") == item_id for i in items.get("items", [])):
            return {"ok": False, "error": f"item {item_id!r} already exists — do not rewrite it."}
        demanded = demanded_items(state.load_artifact())
        if item_id not in demanded:
            if not demanded:
                return {"ok": False, "error": "no undeclared item is referenced yet — an item is "
                        "authored only where a hotspot/effect/gate names it. Add the reference first."}
            return {"ok": False, "error": f"item {item_id!r} is not referenced anywhere — items are "
                    f"authored only where used. Author one of {demanded} (each is named by a "
                    f"hotspot/effect/gate), or add the reference first."}
        full = {**content, "id": item_id}
        err = v_item_one(full)
        if err:
            return {"ok": False, "error": err}
        items.setdefault("items", []).append(full)
        state.write_component("items", items)
        return {"ok": True, "item_id": item_id}

    def write_node(node_id: str, content, story_state_delta: Optional[Dict] = None,
                   force: bool = False, beat: Optional[str] = None,
                   storyline: Optional[str] = None, **delta_fields) -> Dict:
        """Fused: write one IR node into `nodes` AND merge its story-state delta.

        `content` is an IR node object ({lines, end}); escaping/rendering is the compiler's
        job. Producing the dialogue and the continuity bookkeeping in one call keeps them
        consistent — the next node reads the updated story state, never prior script.
        force: a human-driven rewrite may overwrite a locked nodes component (override).
        beat: the story beat this scene realizes — system-stamped (the slot picker owns it),
        not in TOOL_SCHEMAS, so the author never sets it.
        """
        _require_frozen()
        if not force and _locked("nodes"):
            return _locked_error("nodes")
        if node_id == "start":
            return {"ok": False, "error": "do not use 'start' as a node id — the compiler "
                                          "adds 'label start' that jumps to the first node"}
        content = _coerce_json(content)
        if beat and isinstance(content, dict):
            content["beat"] = beat
        if storyline and isinstance(content, dict):
            content["storyline"] = storyline
        from maestro.modules.scenes import node_write_error, normalize_narration
        err = node_write_error(content, min_lines=_node_min_lines)
        if err:
            return {"ok": False, "error": err}
        normalize_narration(content)
        from maestro.story_state import init_story_state, apply_delta

        # Tolerate a malformed story_state_delta (the model sometimes passes a list/str).
        delta = dict(story_state_delta) if isinstance(story_state_delta, dict) else {}
        delta.update({k: v for k, v in delta_fields.items() if k in _DELTA_FIELDS})

        ns = state.read_component("nodes") or {"nodes": {}, "node_ids": []}
        # An overwrite must not orphan the system-stamped beat (mirrors edit_node's
        # full-content path) — losing it re-fans the beat's slot into a duplicate scene.
        prior = ns.get("nodes", {}).get(node_id)
        if isinstance(prior, dict) and "beat" not in content and "beat" in prior:
            content["beat"] = prior["beat"]
        if isinstance(prior, dict) and "storyline" not in content and "storyline" in prior:
            content["storyline"] = prior["storyline"]
        ns.setdefault("nodes", {})[node_id] = content
        ns.setdefault("node_ids", [])
        if node_id not in ns["node_ids"]:
            ns["node_ids"].append(node_id)
        # The event_summary doubles as this node's synopsis — the breadcrumb later scenes see in
        # CURRENT NODES / OPEN SLOTS so they continue the arc instead of re-treading a sibling.
        summary = delta.get("event_summary")
        if summary:
            ns.setdefault("synopses", {})[node_id] = summary
        state.write_component("nodes", ns)

        if delta:
            ss = state.read_story_state() or init_story_state(spec.get("story_state_schema", {}))
            apply_delta(ss, delta)
            state.write_story_state(ss)

        return {"ok": True, "node_id": node_id}

    def write_scene(node_id: str, script: str, end, location: Optional[str] = None,
                    story_state_delta: Optional[Dict] = None, force: bool = False,
                    beat: Optional[str] = None, storyline: Optional[str] = None,
                    **delta_fields) -> Dict:
        """Screenplay-text front end to write_node: parse `NAME: text` lines into IR lines, then
        store through the same validated path. Keeps the model writing dialogue as dialogue
        instead of inside JSON string arrays."""
        from maestro.modules.scenes import parse_screenplay
        chars = (state.read_component("characters") or {}).get("characters", [])
        lines, err = parse_screenplay(script, chars)
        if err:
            return {"ok": False, "error": err}
        content: Dict = {"lines": lines, "end": _coerce_json(end)}
        if location:
            content["location"] = location
        return write_node(node_id, content, story_state_delta=story_state_delta,
                          force=force, beat=beat, storyline=storyline, **delta_fields)

    _UNSET = object()

    def edit_node(node_id: str, line_index: Optional[int] = None, text: Optional[str] = None,
                  speaker=_UNSET, emotion: Optional[str] = None,
                  effects: Optional[List] = None, end: Optional[Dict] = None,
                  location: Optional[str] = None,
                  content: Optional[Dict] = None, force: bool = False) -> Dict:
        """Patch ONE field of a node without rewriting it: a single line (by index), the `end`, or
        the `location` (background id). Repointing a target, fixing a line, or tagging a scene's
        background, without disturbing the rest.
        content: a human edit may instead replace the WHOLE node ({lines, end}) at once.
        force: a human edit may patch a locked nodes component (override)."""
        _require_frozen()
        if not force and _locked("nodes"):
            return _locked_error("nodes")
        ns = state.read_component("nodes") or {}
        nodes = ns.get("nodes", {})
        if node_id not in nodes:
            return {"ok": False, "error": f"no node {node_id!r} to edit"}
        if content is not None:
            content = _coerce_json(content)
            from maestro.modules.scenes import node_write_error, normalize_narration
            err = node_write_error(content, min_lines=_node_min_lines)
            if err:
                return {"ok": False, "error": err}
            # The beat is system-stamped and absent from TOOL_SCHEMAS, so a full-content
            # replace would silently orphan it and the loop would re-fan the beat's slot.
            if "beat" not in content and "beat" in nodes[node_id]:
                content["beat"] = nodes[node_id]["beat"]
            if "storyline" not in content and "storyline" in nodes[node_id]:
                content["storyline"] = nodes[node_id]["storyline"]
            nodes[node_id] = normalize_narration(content)
            state.write_component("nodes", ns)
            return {"ok": True, "node_id": node_id}
        node = nodes[node_id]
        if location is not None:
            bgs = _known_bgs()
            if bgs and location not in bgs:
                return {"ok": False, "error": f"location {location!r} is not in asset_manifest — "
                                              f"use one of {bgs}"}
            node["location"] = location
        if end is not None:
            from maestro.modules.scenes import end_error
            err = end_error(end)
            if err:
                return {"ok": False, "error": err}
            node["end"] = end
        if line_index is not None:
            lines = node.get("lines", [])
            if not (0 <= line_index < len(lines)):
                return {"ok": False, "error": f"line_index {line_index} out of range "
                                              f"(node {node_id} has {len(lines)} lines)"}
            if text is not None:
                if not text.strip():
                    return {"ok": False, "error": "a line's text cannot be empty — to remove a "
                                                  "line, replace the whole node via `content`"}
                lines[line_index]["text"] = text
            if speaker is not _UNSET:
                # Coerce the shapes models actually send (observed: {'id': 'x'} and ['x']
                # retried 100+ steps against a reject) — the intent is unambiguous.
                if isinstance(speaker, dict) and isinstance(speaker.get("id"), str):
                    speaker = speaker["id"]
                elif isinstance(speaker, list) and len(speaker) == 1 \
                        and isinstance(speaker[0], str):
                    speaker = speaker[0]
                if not is_narration_speaker(speaker) and not isinstance(speaker, str):
                    return {"ok": False, "error": "speaker must be a character id STRING (or null "
                            "for narration), not an object/list"}
                lines[line_index]["speaker"] = None if is_narration_speaker(speaker) else speaker
            if emotion is not None:
                if emotion not in _EMOTIONS:
                    return {"ok": False, "error": f"emotion must be one of {sorted(_EMOTIONS)}"}
                lines[line_index]["emotion"] = emotion
            if effects is not None:
                lines[line_index]["effects"] = effects
        state.write_component("nodes", ns)
        return {"ok": True, "node_id": node_id}

    def _resolve_feature_spawns(places: Dict) -> None:
        """A layout-authored move may name its arrival as {"feature": "<id in the TARGET zone>"}.
        Zones are written in any order, so resolution runs after every write, both directions:
        any move whose target zone now has anchors gets its spawn cell filled in."""
        all_places = places.get("places") or {}
        for p in all_places.values():
            for h in (p.get("interactables") or []) if isinstance(p, dict) else []:
                a = h.get("action") if isinstance(h, dict) else None
                if not isinstance(a, dict) or a.get("type") != "move":
                    continue
                spawn = a.get("spawn")
                feat = (spawn or {}).get("feature") if isinstance(spawn, dict) else None
                if not feat:
                    continue
                target = all_places.get(a.get("target"))
                t_anchors = (target or {}).get("anchors") or {}
                anchor = t_anchors.get(feat)
                if anchor is None and t_anchors:
                    # The model names arrivals by features IT knows — its own zone's (observed:
                    # arriving at the source's campfire). Arriving at the target's gate is the
                    # deterministic best guess; the round-trip rule keeps play coherent.
                    exits = [k for k in sorted(t_anchors) if k.startswith("x_")]
                    anchor = t_anchors[exits[0]] if exits else t_anchors[sorted(t_anchors)[0]]
                if anchor:
                    a["spawn"] = {"cell": {"x": anchor["x"], "y": anchor["y"]}}

    def write_place(place_id: str, content, force: bool = False) -> Dict:
        """Write one place (background + interactables) into `places`, mirroring write_node.
        The scaffold (goal/items/flags/start_place) is laid by set_places_meta.

        A walkable place is authored as a LAYOUT (features on a coarse region grid) — the model
        plans, maestro.map_builder rasterizes deterministically, and the stored `tiles` grid is
        valid by construction. An interactable's position may be {"feature": "<layout id>"};
        it resolves to that feature's anchor cell here.
        force: a human edit may overwrite a locked places component (override, like write_component/
        write_node/edit_node). The agent never sets it — not in TOOL_SCHEMAS."""
        _require_frozen()
        if not force and _locked("places"):
            return _locked_error("places")
        content = _coerce_json(content)
        if isinstance(content, dict) and isinstance(content.get("layout"), dict):
            from maestro.map_builder import build_tiles, v_layout
            lerr = v_layout(content["layout"])
            if lerr:
                return {"ok": False, "error": lerr}
            built = build_tiles(place_id, content["layout"])
            content["tiles"] = {"rows": built["rows"], "legend": built["legend"]}
            content["anchors"] = built["anchors"]
            content["footprints"] = built["footprints"]
            anchors = built["anchors"]
            used: Dict = {}
            for h in content.get("interactables") or []:
                pos = h.get("position") if isinstance(h, dict) else None
                feat = (pos or {}).get("feature")
                if feat is not None:
                    if feat not in anchors:
                        return {"ok": False, "error":
                                f"interactable {h.get('id')!r} sits at feature {feat!r} which "
                                f"the layout doesn't declare — use one of {sorted(anchors)}"}
                    a = anchors[feat]
                    # several hotspots on one feature fan out around its anchor; the fanned
                    # cell snaps back to open ground if the offset lands on a wall
                    n = used.get(feat, 0)
                    used[feat] = n + 1
                    dx = (0, 1, -1, 0)[n % 4]
                    dy = (0, 0, 0, 1)[n % 4]
                    from maestro.map_builder import snap_to_open
                    taken = {(i["position"]["cell"]["x"], i["position"]["cell"]["y"])
                             for i in content.get("interactables", [])
                             if i is not h and (i.get("position") or {}).get("cell")}
                    cell = snap_to_open(content["tiles"], a["x"] + dx, a["y"] + dy, taken) \
                        or (a["x"], a["y"])
                    h["position"] = {"cell": {"x": cell[0], "y": cell[1]}}
        err = _place_content_error(content)
        if err:
            return {"ok": False, "error": err}
        places = state.read_component("places") or {"place_ids": [], "places": {}}
        places.setdefault("places", {})[place_id] = content
        places.setdefault("place_ids", [])
        if place_id not in places["place_ids"]:
            places["place_ids"].append(place_id)
        if not places.get("start_place"):
            places["start_place"] = place_id
        _resolve_feature_spawns(places)
        state.write_component("places", places)
        return {"ok": True, "place_id": place_id}

    def set_places_meta(goal=None, flags=None, variables=None, start_place=None,
                        start_spawn=None, **ignored) -> Dict:
        """Declare the scaffold on `places` — win goal, flags, variables, start_place, and (walkable
        RPG only) start_spawn, the player's arrival TILE in start_place. write_place never sets these.
        Merges: only the fields passed change. Stray kwargs (e.g. the model jamming `nodes=` or
        `items=` here) are ignored, not a crash — but note them so the model learns this tool can't
        touch that."""
        if ignored:
            return {"ok": False, "error":
                    f"set_places_meta does not take {sorted(ignored)} — it only declares goal/flags/"
                    f"variables/start_place/start_spawn. Items live in the `items` catalogue "
                    f"(write_component('items', ...)); for hotspots use add_interactable/edit_place."}
        if start_spawn is not None and _cell_xy(start_spawn) is None:
            return {"ok": False, "error":
                    "start_spawn must be a tile {\"cell\": {\"x\": <int>, \"y\": <int>}} — the "
                    "player's start tile on a walkable map"}
        _require_frozen()
        if _locked("places"):
            return _locked_error("places")
        if goal is not None and (not isinstance(goal, dict)
                                 or goal.get("type") not in ("flag", "room") or not goal.get("id")):
            return {"ok": False, "error":
                    "goal must be {\"type\": \"flag\" or \"room\", \"id\": \"<the winning flag or "
                    "place id>\"} — e.g. {\"type\": \"flag\", \"id\": \"escaped\"}"}
        places = state.read_component("places") or {"place_ids": [], "places": {}}
        if goal is not None:
            places["goal"] = goal
        if flags is not None:
            places["flags"] = flags
        if variables is not None:
            places["variables"] = variables
        if start_place is not None:
            places["start_place"] = start_place
        if start_spawn is not None:
            places["start_spawn"] = start_spawn
        state.write_component("places", places)
        return {"ok": True, "goal": places.get("goal"),
                "flags": places.get("flags"), "start_place": places.get("start_place"),
                "start_spawn": places.get("start_spawn")}

    def edit_place(place_id: str, interactable_id: str, action: Optional[Dict] = None,
                   position: Optional[Dict] = None, label: Optional[str] = None) -> Dict:
        """Patch ONE interactable — replace its action (repoint a move / fix a use), position,
        or label — without rewriting the place (which tends to drop other interactables)."""
        _require_frozen()
        if _locked("places"):
            return _locked_error("places")
        places = state.read_component("places") or {}
        place = (places.get("places") or {}).get(place_id)
        if place is None:
            return {"ok": False, "error": f"no place {place_id!r} to edit"}
        h = next((i for i in place.get("interactables", []) if i.get("id") == interactable_id), None)
        if h is None:
            return {"ok": False, "error": f"no interactable {interactable_id!r} in place {place_id!r}"}
        if action is not None:
            from maestro.modules.world import action_error
            # Same-type patches MERGE over the current action (a null value removes its key).
            # Replacement semantics let two fixers fight: the wiring fix's `requires` wiped the
            # spawn fix's `spawn` and vice versa, each edit 'ok' — an A-B loop the stall
            # detector can't see. A type CHANGE still replaces outright.
            current = h.get("action") or {}
            if isinstance(action, dict) and action.get("type") == current.get("type"):
                action = {k: v for k, v in {**current, **action}.items() if v is not None}
            err = action_error(action)
            if err:
                return {"ok": False, "error": err}
            h["action"] = action
        if position is not None:
            cell = (position or {}).get("cell") or {}
            if place.get("kind") in ("world_map", "town", "interior") \
                    and isinstance(cell.get("x"), int) and isinstance(cell.get("y"), int):
                from maestro.map_builder import snap_to_open
                taken = {(i["position"]["cell"]["x"], i["position"]["cell"]["y"])
                         for i in place.get("interactables", [])
                         if i is not h and (i.get("position") or {}).get("cell")}
                snapped = snap_to_open(place.get("tiles") or {}, cell["x"], cell["y"], taken)
                if snapped:
                    position = {"cell": {"x": snapped[0], "y": snapped[1]}}
            h["position"] = position
        if label is not None:
            h["label"] = label
        _resolve_feature_spawns(places)
        state.write_component("places", places)
        return {"ok": True, "place_id": place_id, "interactable_id": interactable_id}

    def add_interactable(place_id: str, interactable: Dict) -> Dict:
        """APPEND one new interactable to an existing place — without rewriting it (write_place
        clobbers the others) and without cannibalizing an existing hotspot (edit_place repoints,
        which breaks that hotspot's current route). This is the right tool to ADD a move hotspot
        for an unreachable place, or a use/win hotspot to set the goal flag."""
        _require_frozen()
        if _locked("places"):
            return _locked_error("places")
        if not isinstance(interactable, dict) or not interactable.get("id"):
            return {"ok": False, "error": "interactable must be an object with an 'id'"}
        from maestro.modules.world import action_error
        err = action_error(interactable.get("action"))
        if err:
            return {"ok": False, "error": err}
        places = state.read_component("places") or {}
        place = (places.get("places") or {}).get(place_id)
        if place is None:
            return {"ok": False, "error": f"no place {place_id!r}"}
        inter = place.setdefault("interactables", [])
        if any(i.get("id") == interactable["id"] for i in inter):
            return {"ok": False, "error":
                    f"interactable {interactable['id']!r} already exists in {place_id!r} — "
                    f"use edit_place to change it, or pick a new id"}
        if place.get("kind") in ("world_map", "town", "interior"):
            from maestro.map_builder import snap_to_open
            pos = interactable.get("position") or {}
            feat = pos.get("feature")
            anchors = place.get("anchors") or {}
            if feat is not None:
                a = anchors.get(feat)
                if a is None:
                    return {"ok": False, "error":
                            f"no feature {feat!r} in {place_id!r} — use one of {sorted(anchors)}"}
                interactable["position"] = {"cell": {"x": a["x"], "y": a["y"]}}
            cell = (interactable.get("position") or {}).get("cell") or {}
            if isinstance(cell.get("x"), int) and isinstance(cell.get("y"), int):
                taken = {(i["position"]["cell"]["x"], i["position"]["cell"]["y"])
                         for i in inter if (i.get("position") or {}).get("cell")}
                snapped = snap_to_open(place.get("tiles") or {}, cell["x"], cell["y"], taken)
                if snapped:
                    interactable["position"] = {"cell": {"x": snapped[0], "y": snapped[1]}}
        inter.append(interactable)
        state.write_component("places", places)
        return {"ok": True, "place_id": place_id, "interactable_id": interactable["id"]}

    def read_place(place_id: str) -> Dict:
        place = (state.read_component("places") or {}).get("places", {}).get(place_id)
        if place is None:
            return {"ok": False, "error": f"no place {place_id!r}"}
        return {"ok": True, "place_id": place_id, "content": place}

    # ── combat: the doc is grown ONE slice at a time (dependency order), each validated against the
    #    already-declared upstream ids at write time. set_combat_meta lays stats/statuses; the write_*
    #    tools id-merge into the list slices (replace-by-id, else append). ──────────────────────────
    def _combat_write(slice_key, item_id, content, validate):
        _require_frozen()
        if _locked("combat"):
            return _locked_error("combat")
        content = _coerce_json(content)
        if not isinstance(content, dict):
            return {"ok": False, "error": "content must be a JSON object"}
        combat = state.read_component("combat") or {}
        if not combat.get("stats"):
            return {"ok": False, "error": "declare the stat system first with "
                    "set_combat_meta(combat_model=..., stats=[...]) before authoring " + slice_key}
        full = {**content, "id": item_id}
        err = validate(full, combat)
        if err:
            return {"ok": False, "error": err}
        rows = combat.setdefault(slice_key, [])
        for i, row in enumerate(rows):
            if isinstance(row, dict) and row.get("id") == item_id:
                rows[i] = full
                break
        else:
            rows.append(full)
        state.write_component("combat", combat)
        return {"ok": True, "id": item_id}

    def set_combat_meta(combat_model=None, stats=None, statuses=None, **ignored) -> Dict:
        """Lay the combat foundation on `combat`: the combat_model + the stat SYSTEM (you need an
        hp-like resource_depletable stat) + any statuses. Merges (pass only what changes). The
        abilities/combatants/encounters are grown by write_ability/write_combatant/write_encounter."""
        if ignored:
            return {"ok": False, "error":
                    f"set_combat_meta does not take {sorted(ignored)} — only combat_model/stats/"
                    f"statuses. Abilities/combatants/encounters use write_ability/write_combatant/"
                    f"write_encounter."}
        _require_frozen()
        if _locked("combat"):
            return _locked_error("combat")
        combat = state.read_component("combat") or {}
        model = combat_model if combat_model is not None else combat.get("combat_model", "turn_based")
        new_stats = stats if stats is not None else combat.get("stats")
        new_statuses = statuses if statuses is not None else combat.get("statuses", [])
        err = combat_meta_error(model, new_stats, new_statuses)
        if err:
            return {"ok": False, "error": err}
        combat["combat_model"] = model
        if stats is not None:
            combat["stats"] = stats
        if statuses is not None:
            combat["statuses"] = statuses
        combat.setdefault("statuses", new_statuses or [])
        state.write_component("combat", combat)
        return {"ok": True, "combat_model": model,
                "stats": [s.get("id") for s in combat.get("stats", [])],
                "statuses": [s.get("id") for s in combat.get("statuses", [])]}

    def write_ability(ability_id: str, content) -> Dict:
        """Author ONE combat ability into `combat` (validated against the declared stats/statuses)."""
        return _combat_write("abilities", ability_id, content, ability_write_error)

    def write_combatant(combatant_id: str, content) -> Dict:
        """Author ONE combatant into `combat` (validated against the declared stats/abilities)."""
        return _combat_write("combatants", combatant_id, content, combatant_write_error)

    def write_encounter(encounter_id: str, content) -> Dict:
        """Author ONE encounter into `combat` (validated against the declared combatants)."""
        return _combat_write("encounters", encounter_id, content, encounter_write_error)

    def set_progression(progression) -> Dict:
        """Declare combat's growth loop: who the player fights as, which leveled VARIABLE is the
        XP pool, and per-level stat growth. Declares the xp/level variable pair on `places` as
        ordinary variables — XP producers are then plain effects (victory yields, harvest
        outcomes, choices) and any condition can gate on the level."""
        from maestro.modules.combat import progression_error
        _require_frozen()
        if _locked("combat"):
            return _locked_error("combat")
        progression = _coerce_json(progression)
        if not isinstance(progression, dict):
            return {"ok": False, "error": "progression must be a JSON object"}
        xp_var = progression.get("xp_var") or "xp"
        level_var = progression.get("level_var") or "level"
        per_level = progression.get("per_level", 20)
        if not isinstance(per_level, int) or per_level < 1:
            return {"ok": False, "error": "per_level must be a positive integer"}
        combat = state.read_component("combat") or {}
        prog = {"player": progression.get("player"), "xp_var": xp_var}
        if progression.get("growth") is not None:
            prog["growth"] = progression["growth"]
        err = progression_error(prog, combat)
        if err:
            return {"ok": False, "error": err}
        # The variable pair is declared by CODE, deterministically — additive, so it bypasses
        # the places done-lock rather than parking progression behind it.
        places = state.read_component("places") or {"place_ids": [], "places": {}}
        variables = places.setdefault("variables", [])
        variables[:] = [v for v in variables
                        if (v.get("id") if isinstance(v, dict) else v) not in (xp_var, level_var)]
        variables.append({"id": xp_var, "default": 0,
                          "level_var": level_var, "per_level": per_level})
        variables.append({"id": level_var, "default": 1})
        combat["progression"] = prog
        state.write_component("places", places)
        state.write_component("combat", combat)
        return {"ok": True, "player": prog["player"], "xp_var": xp_var,
                "level_var": level_var, "per_level": per_level}

    def set_encounter_table(place_id: str, table) -> Dict:
        """Give ONE walkable zone a wild-fight table: each step on open ground rolls `rate`; a
        hit fights one combatant drawn from `entries` by weight, stats scaled by `tier`."""
        from maestro.modules.wild_encounters import encounter_table_error
        _require_frozen()
        if _locked("places"):
            return _locked_error("places")
        table = _coerce_json(table)
        places = state.read_component("places") or {}
        place = (places.get("places") or {}).get(place_id)
        if place is None:
            return {"ok": False, "error": f"no place {place_id!r}"}
        if place.get("kind") not in ("world_map", "town", "interior"):
            return {"ok": False, "error":
                    f"{place_id!r} is a {place.get('kind')!r} — encounter tables belong on "
                    f"walkable zones (world_map/town/interior)"}
        err = encounter_table_error(table, state.read_component("combat") or {})
        if err:
            return {"ok": False, "error": err}
        place["encounter_table"] = table
        state.write_component("places", places)
        return {"ok": True, "place_id": place_id}

    def generate_asset() -> Dict:
        """Generate the image assets the asset_manifest declares (wraps comfyui)."""
        _require_frozen()
        from renpy.fns import generate_images
        presentation = (state.read_spec() or {}).get("presentation", "2d")
        result = generate_images(state.load_artifact(), state.run_dir, presentation=presentation)
        return {"ok": result.get("status") == "ok", **result}

    # ── inspection (safe pre-freeze) ─────────────────────────────────────────
    def read_component(component_id: str) -> Dict:
        content = state.read_component(component_id)
        if content is None:
            return {"ok": False, "error": f"no component {component_id!r}"}
        return {"ok": True, "component_id": component_id, "content": content}

    def read_node(node_id: str) -> Dict:
        nodes = (state.read_component("nodes") or {}).get("nodes", {})
        if node_id not in nodes:
            return {"ok": False, "error": f"no node {node_id!r}"}
        return {"ok": True, "node_id": node_id, "content": nodes[node_id]}

    def read_story_state() -> Dict:
        from maestro.story_state import init_story_state
        return {"ok": True,
                "story_state": state.read_story_state() or init_story_state(spec.get("story_state_schema", {}))}

    def validate_tool(component_id: Optional[str] = None) -> Dict:
        from maestro.agent_loop import effective_pairs
        ctx = build_context(spec, state)
        errs = [e for _, e in effective_pairs(modules, ctx)
                if component_id is None or e.component == component_id]
        return {"ok": not errs, "failures": [
            {"component": e.component, "code": e.code, "type": e.type.value, "detail": e.message}
            for e in errs]}

    # No compile tool: the loop already runs the `compiles` done-condition (a real build)
    # after every step, so a manual trigger only wastes a step — and lets the agent compile
    # early, fighting the deliberate when_clean ordering of the compile check.

    def request_review(question: str, options: Optional[List[str]] = None) -> Dict:
        # Scoped escape hatch. Phase 5 surfaces this to the human over the event
        # bus; here it just returns a structured pending marker.
        return {"ok": True, "status": "review_requested",
                "question": question, "options": options or []}

    # ── human-only dirty / thumb tools (never in TOOL_SCHEMAS, so the agent can't call them —
    #    same discipline as the `force` param) ─────────────────────────────────────────────────
    def set_dirty(idkey: str, note: str = "") -> Dict:
        """Flag one asset (idkey '<component>:<item_id>') for attention with an optional note; the
        loop then rewrites it and the human's thumbs-up clears it."""
        from maestro.modules import human as human_mod
        human_mod.set_dirty(state, idkey, note)
        return {"ok": True, "idkey": idkey, "note": note}

    def thumbs_up(idkey: str) -> Dict:
        """Approve an asset — clear its dirty flag so the loop stops surfacing it."""
        from maestro.modules import human as human_mod
        return {"ok": True, "idkey": idkey, "cleared": human_mod.clear_dirty(state, idkey)}

    def thumbs_down(idkey: str, note: str = "") -> Dict:
        """Reject an asset with a 'change this' note — sets it dirty, handing it back to the loop."""
        from maestro.modules import human as human_mod
        human_mod.set_dirty(state, idkey, note)
        return {"ok": True, "idkey": idkey, "note": note}

    return {
        "write_component": write_component,
        "add_character": add_character,
        "set_spine": set_spine,
        "add_storyline": add_storyline,
        "add_beat": add_beat,
        "finish_storyline": finish_storyline,
        "set_bible": set_bible,
        "add_faction": add_faction,
        "add_tension": add_tension,
        "add_item": add_item,
        "write_node": write_node,
        "write_scene": write_scene,
        "edit_node": edit_node,
        "write_place": write_place,
        "edit_place": edit_place,
        "add_interactable": add_interactable,
        "read_place": read_place,
        "set_places_meta": set_places_meta,
        "set_combat_meta": set_combat_meta,
        "write_ability": write_ability,
        "write_combatant": write_combatant,
        "write_encounter": write_encounter,
        "set_progression": set_progression,
        "set_encounter_table": set_encounter_table,
        "read_component": read_component,
        "read_node": read_node,
        "read_story_state": read_story_state,
        "generate_asset": generate_asset,
        "validate": validate_tool,
        "request_review": request_review,
        "set_dirty": set_dirty,
        "thumbs_up": thumbs_up,
        "thumbs_down": thumbs_down,
    }
