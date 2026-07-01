"""Artifact tools — the frequent, autonomous capabilities the executor dispatches.

Bound to one run's durable state. build_tools(spec, state) returns the {name: fn}
registry the Executor consumes; the agent chooses which to call and when.

The build tools (write_component, generate_asset) refuse to run until the spec is
frozen — the human gate is load-bearing. read_component, validate, compile_renpy
and update_scratchpad are safe before freezing.

State is bounded on purpose: there is no raw read_file/write_file. Components are
written by id; scratchpad is replaced, not appended.
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

_END_TYPES = {"jump", "menu", "return", "end"}
_EMOTIONS = set(EMOTIONS)
# A menu is a dramatic fork, not a location picker. Capped so the slot-driven loop can't satisfy
# its node quota by fanning one node into a wide hub of stub branches (the hub-and-spoke star that
# guts the arc); past this, the model must build DEPTH — scenes that lead into scenes — instead.
_MAX_MENU_CHOICES = 3


# The model intuitively writes a speaker STRING for narration ("narration"/"narrator") instead of
# the convention speaker:null. Left alone it isn't a declared character, so it only blows up far
# later at crossref/compile — where a small model thrashes trying to "fix" it. Normalize at write
# time so the intent (narration) is honored and the error never forms (is_narration_speaker is the
# shared rule, also applied as a backstop in ir_assemble).
def _coerce_json(value):
    """A small model frequently passes a nested object (a node's content, a place/match body) as a
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


def _normalize_narration(content):
    """In-place: any line whose speaker reads as narration becomes speaker:null."""
    if isinstance(content, dict):
        for ln in content.get("lines", []) or []:
            if isinstance(ln, dict) and is_narration_speaker(ln.get("speaker")):
                ln["speaker"] = None
    return content


def _node_content_error(content) -> Optional[str]:
    """Reject a malformed IR node object up front so a wrong shape steers immediately
    instead of failing schema/crossref/compile later."""
    if not isinstance(content, dict):
        return "node content must be a JSON object {lines, end}"
    lines = content.get("lines")
    if not isinstance(lines, list) or not lines:
        return "node.lines must be a non-empty list of {speaker, text} objects"
    for j, ln in enumerate(lines):
        if not isinstance(ln, dict) or not ln.get("text"):
            return f"node.lines[{j}] needs a non-empty 'text' (speaker is optional; null = narration)"
    end = content.get("end")
    if not isinstance(end, dict) or end.get("type") not in _END_TYPES:
        return f"node.end must be an object whose 'type' is one of {sorted(_END_TYPES)}"
    if end.get("type") == "menu":
        choices = end.get("choices") or []
        n = len(choices)
        if n > _MAX_MENU_CHOICES:
            return (f"this menu has {n} choices — a menu is a DRAMATIC FORK, at most "
                    f"{_MAX_MENU_CHOICES} divergent paths, not a room/location picker. Cut it to "
                    f"the {_MAX_MENU_CHOICES} choices that actually matter; for linear flow use "
                    f"end.type 'jump' and let the NEXT scene branch. Build depth, not width.")
        if choices and all(isinstance(c, dict) and c.get("requires") for c in choices):
            return ("every choice in this menu is gated by `requires` — if none match at runtime the "
                    "menu is empty and the game dead-ends. Leave at least ONE choice ungated as a "
                    "guaranteed fallback path.")
        targets = {c.get("target") for c in choices if isinstance(c, dict) and c.get("target")}
        if len(choices) >= 2 and len(targets) < 2:
            return ("every choice in this menu leads to the SAME scene — that's a fake choice, not a "
                    "fork. Either make the choices lead to DIFFERENT targets (a real branch), or drop "
                    "the menu and use end.type 'jump' for a single continuation.")
    return None


_CARD_MODELS = {"high_card", "blackjack"}


def _match_content_error(content) -> Optional[str]:
    if not isinstance(content, dict):
        return "match content must be a JSON object {card_model, opponent, ante}"
    if content.get("card_model") not in _CARD_MODELS:
        return f"match.card_model must be one of {sorted(_CARD_MODELS)}"
    if not content.get("opponent"):
        return "match needs an 'opponent' (a characters component id)"
    ante = content.get("ante")
    if not isinstance(ante, dict) or not ante.get("var") or "amount" not in ante:
        return "match.ante must be {var, amount} — the staked variable and how much"
    return None


def _action_validator():
    """A JSON-Schema validator for a single interactable action, built once from the IR schema's
    $defs/action — so write-time checks stay sourced from the one schema, not a hand-rolled copy."""
    import json
    from pathlib import Path
    import jsonschema
    schema = json.loads((Path(__file__).resolve().parents[2] / "docs" / "game_ir.schema.json")
                        .read_text(encoding="utf-8"))
    return jsonschema.Draft202012Validator({"$ref": "#/$defs/action", "$defs": schema["$defs"]})


_ACTION_VALIDATOR = None


def _action_struct_error(action) -> Optional[str]:
    """Reject a malformed action at write time with an ACTIONABLE message — so the sub-loop fixes
    it in one edit instead of discovering it at compile 20 steps later with the schema's opaque
    'not valid under any of the given schemas'. Friendly hints for the common traps; the schema is
    the backstop for the rest."""
    if not isinstance(action, dict) or not action.get("type"):
        return "action needs an object with a 'type'"
    # The trap we keep hitting: a use clause with an empty `requires` ({}), which is not a valid
    # condition. An unconditional outcome belongs in `fallback`, not a clause.
    if action.get("type") == "use":
        for i, cl in enumerate(action.get("clauses", []) or []):
            req = cl.get("requires") if isinstance(cl, dict) else None
            if not isinstance(req, dict) or not req:
                return (f"use clause[{i}].requires must be a REAL condition — e.g. "
                        f'{{"flag":"x"}}, {{"item":"y"}}, or {{"var":"g","op":">=","value":10}}. '
                        f"For an outcome that ALWAYS fires, DROP the clause and put it in `fallback`: "
                        f'{{"type":"use","fallback":{{"text":"...","effects":[...]}}}}.')
    global _ACTION_VALIDATOR
    if _ACTION_VALIDATOR is None:
        _ACTION_VALIDATOR = _action_validator()
    errs = sorted(_ACTION_VALIDATOR.iter_errors(action), key=lambda e: len(list(e.path)))
    if errs:
        loc = "/".join(str(p) for p in errs[0].path) or "action"
        return f"action invalid at {loc}: {errs[0].message}"
    return None


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
        err = _action_struct_error(h.get("action"))
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
        "name": "edit_node",
        "description": "Patch ONE field of an existing node without rewriting it: replace a "
                       "single line by index (text/speaker/emotion/effects) or replace the node's "
                       "`end`. Use to repoint a jump/menu target or fix one line.",
        "parameters": {"type": "object", "properties": {
            "node_id": {"type": "string"},
            "line_index": {"type": "integer", "description": "index into lines to patch (0-based)"},
            "text": {"type": "string", "description": "new text for that line"},
            "speaker": {"description": "new speaker id for that line (null for narration)"},
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
                       "structured `action`), plus a backdrop (room) or a tile map (walkable). Adds "
                       "it to place_ids. A place is either a point-and-click room (kind 'room', "
                       "pixel-rect hotspots + background) OR a walkable RPG map (kind "
                       "'world_map'/'town'/'interior', a `tiles` char grid + cell positions).",
        "parameters": {"type": "object", "properties": {
            "place_id": {"type": "string", "description": "e.g. 'room_kitchen' or 'zone_crypt'"},
            "content": {"type": "object", "description":
                "{kind: 'room' (point-and-click) | 'world_map'|'town'|'interior' (walkable RPG), "
                "interactables: [{id, label, position, action:{type, ...}}], "
                "background: <asset id> (ROOM only), "
                "tiles: {legend:{<char>:{role:'open'|'blocked', theme}}, rows:['..','..']} (RPG "
                "ONLY, required — the map painted as char rows; grid size = shape of rows). A "
                "position is {rect:{x,y,w,h}} for a room or {cell:{x,y}} for an RPG tile."},
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
        "name": "write_match",
        "description": "Write one wagering card match into `matches`: its card_model (high_card or "
                       "blackjack), opponent (a characters component id), ante {var, amount}, and "
                       "on_win/on_lose payout. Adds it to match_ids. Card games only.",
        "parameters": {"type": "object", "properties": {
            "match_id": {"type": "string", "description": "e.g. 'match_gambler'"},
            "content": {"type": "object", "description":
                "{card_model: 'high_card'|'blackjack', deck_model?: 'standard_52', "
                "opponent: <char id>, ante: {var, amount}, rounds?: int, "
                "on_win: {effects?, end?}, on_lose: {effects?, end?}}"},
        }, "required": ["match_id", "content"]}}},
    {"type": "function", "function": {
        "name": "edit_match",
        "description": "Patch fields of an existing card match without rewriting it (card_model, "
                       "opponent, ante, rounds, on_win, on_lose). Card games only.",
        "parameters": {"type": "object", "properties": {
            "match_id": {"type": "string"},
            "card_model": {"type": "string"},
            "opponent": {"type": "string"},
            "ante": {"type": "object", "description": "{var, amount}"},
            "rounds": {"type": "integer"},
            "on_win": {"type": "object", "description": "{effects?, end?}"},
            "on_lose": {"type": "object", "description": "{effects?, end?}"},
        }, "required": ["match_id"]}}},
    {"type": "function", "function": {
        "name": "read_match",
        "description": "Read one card match's current definition.",
        "parameters": {"type": "object", "properties": {
            "match_id": {"type": "string"}}, "required": ["match_id"]}}},
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
        "name": "update_scratchpad",
        "description": "Replace your working memory (current goal, recent decisions, open questions).",
        "parameters": {"type": "object", "properties": {
            "current_goal": {"type": "string"},
            "recent_decisions": {"type": "array", "items": {"type": "string"}},
            "open_questions": {"type": "array", "items": {"type": "string"}},
        }, "required": []}}},
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
    # VN (a dialogue spine, the compile terminal over `nodes`) demands a background per scene; reject
    # an untagged node at creation rather than adding locations one edit at a time in a fix phase.
    _node_needs_location = any(getattr(m, "emits_compile", False)
                               and getattr(m, "component", None) == "nodes" for m in modules)

    # A component LOCKS once no module reports an error ON it: a full rewrite would otherwise drop
    # ids other components already reference. The sweep covers every module that AFFECTS the
    # component (owner + cross-cutting like `state` — a state_wiring error saying "cut the
    # declaration" on `items` must keep `items` writable) and open human todos targeting it.
    # EXCEPTION: the compile terminal (emits_compile) stays writable until the build ends (its
    # correctness depends on the whole artifact, and we never run its expensive compile just to
    # test a lock).
    _owner = {cid: m for m in modules for cid in m.affected_components()}
    _terminal = {getattr(m, "component", None) for m in modules if getattr(m, "emits_compile", False)}

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
        if not force and _locked(component_id):
            return _locked_error(component_id)
        content = _coerce_json(content)
        # Reject the wrong shape up front so it's an immediate steering signal, not a
        # crash inside compile later. The bad content is NOT persisted.
        err = _schema_error(component_id, content)
        if err:
            return {"ok": False, "error": f"invalid {component_id}: {err}"}
        state.write_component(component_id, content)
        return {"ok": True, "component_id": component_id}

    def write_node(node_id: str, content, story_state_delta: Optional[Dict] = None,
                   force: bool = False, beat: Optional[str] = None, **delta_fields) -> Dict:
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
        err = _node_content_error(content)
        if err:
            return {"ok": False, "error": err}
        _normalize_narration(content)
        if len(content["lines"]) < _node_min_lines:
            return {"ok": False, "error":
                    f"a node needs at least {_node_min_lines} lines/beats — this has "
                    f"{len(content['lines'])}. Write the FULL scene now (several dialogue beats "
                    f"with subtext), not a stub; thin nodes are rejected."}
        if _node_needs_location and not content.get("location"):
            return {"ok": False, "error":
                    "this node has no `location` — set it to a background id from asset_manifest "
                    "(e.g. 'bg_room'); every scene needs a background. Add \"location\" and resend."}
        from maestro.story_state import init_story_state, apply_delta

        # Tolerate a malformed story_state_delta (the model sometimes passes a list/str).
        delta = dict(story_state_delta) if isinstance(story_state_delta, dict) else {}
        delta.update({k: v for k, v in delta_fields.items() if k in _DELTA_FIELDS})

        ns = state.read_component("nodes") or {"nodes": {}, "node_ids": []}
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
            err = _node_content_error(content)
            if err:
                return {"ok": False, "error": err}
            nodes[node_id] = _normalize_narration(content)
            state.write_component("nodes", ns)
            return {"ok": True, "node_id": node_id}
        node = nodes[node_id]
        if location is not None:
            node["location"] = location
        if end is not None:
            if end.get("type") not in _END_TYPES:
                return {"ok": False, "error": f"end.type must be one of {sorted(_END_TYPES)}"}
            node["end"] = end
        if line_index is not None:
            lines = node.get("lines", [])
            if not (0 <= line_index < len(lines)):
                return {"ok": False, "error": f"line_index {line_index} out of range "
                                              f"(node {node_id} has {len(lines)} lines)"}
            if text is not None:
                lines[line_index]["text"] = text
            if speaker is not _UNSET:
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

    def write_place(place_id: str, content) -> Dict:
        """Write one place (background + interactables) into `places`, mirroring write_node.
        The scaffold (goal/items/flags/start_place) is laid by set_places_meta."""
        _require_frozen()
        if _locked("places"):
            return _locked_error("places")
        content = _coerce_json(content)
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
            err = _action_struct_error(action)
            if err:
                return {"ok": False, "error": err}
            h["action"] = action
        if position is not None:
            h["position"] = position
        if label is not None:
            h["label"] = label
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
        err = _action_struct_error(interactable.get("action"))
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
        inter.append(interactable)
        state.write_component("places", places)
        return {"ok": True, "place_id": place_id, "interactable_id": interactable["id"]}

    def read_place(place_id: str) -> Dict:
        place = (state.read_component("places") or {}).get("places", {}).get(place_id)
        if place is None:
            return {"ok": False, "error": f"no place {place_id!r}"}
        return {"ok": True, "place_id": place_id, "content": place}

    def write_match(match_id: str, content) -> Dict:
        """Write one card match into `matches` (mirrors write_node/write_place)."""
        _require_frozen()
        if _locked("matches"):
            return _locked_error("matches")
        content = _coerce_json(content)
        err = _match_content_error(content)
        if err:
            return {"ok": False, "error": err}
        matches = state.read_component("matches") or {"match_ids": [], "matches": {}}
        matches.setdefault("matches", {})[match_id] = content
        matches.setdefault("match_ids", [])
        if match_id not in matches["match_ids"]:
            matches["match_ids"].append(match_id)
        state.write_component("matches", matches)
        return {"ok": True, "match_id": match_id}

    def edit_match(match_id: str, card_model=None, opponent=None, ante=None,
                   rounds=None, on_win=None, on_lose=None) -> Dict:
        _require_frozen()
        if _locked("matches"):
            return _locked_error("matches")
        matches = state.read_component("matches") or {}
        m = (matches.get("matches") or {}).get(match_id)
        if m is None:
            return {"ok": False, "error": f"no match {match_id!r} to edit"}
        if card_model is not None:
            if card_model not in _CARD_MODELS:
                return {"ok": False, "error": f"card_model must be one of {sorted(_CARD_MODELS)}"}
            m["card_model"] = card_model
        if opponent is not None:
            m["opponent"] = opponent
        if ante is not None:
            m["ante"] = ante
        if rounds is not None:
            m["rounds"] = rounds
        if on_win is not None:
            m["on_win"] = on_win
        if on_lose is not None:
            m["on_lose"] = on_lose
        state.write_component("matches", matches)
        return {"ok": True, "match_id": match_id}

    def read_match(match_id: str) -> Dict:
        m = (state.read_component("matches") or {}).get("matches", {}).get(match_id)
        if m is None:
            return {"ok": False, "error": f"no match {match_id!r}"}
        return {"ok": True, "match_id": match_id, "content": m}

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

    def generate_asset() -> Dict:
        """Generate the image assets the asset_manifest declares (wraps comfyui)."""
        _require_frozen()
        from renpy.fns import generate_images
        result = generate_images(state.load_artifact(), state.run_dir)
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

    # No compile tool: the executor already runs the `compiles` done-condition (a real build)
    # after every step, so a manual trigger only wastes a step — and lets the agent compile early,
    # fighting the deliberate "compiles last" check ordering (see executor._CHECK_PRIORITY).

    # ── working memory ───────────────────────────────────────────────────────
    def update_scratchpad(current_goal: str = "",
                          recent_decisions: Optional[List[str]] = None,
                          open_questions: Optional[List[str]] = None) -> Dict:
        state.write_scratchpad(current_goal, recent_decisions, open_questions)
        return {"ok": True}

    def request_review(question: str, options: Optional[List[str]] = None) -> Dict:
        # Scoped escape hatch. Phase 5 surfaces this to the human over the event
        # bus; here it just returns a structured pending marker.
        return {"ok": True, "status": "review_requested",
                "question": question, "options": options or []}

    return {
        "write_component": write_component,
        "write_node": write_node,
        "edit_node": edit_node,
        "write_place": write_place,
        "edit_place": edit_place,
        "add_interactable": add_interactable,
        "read_place": read_place,
        "set_places_meta": set_places_meta,
        "write_match": write_match,
        "edit_match": edit_match,
        "read_match": read_match,
        "set_combat_meta": set_combat_meta,
        "write_ability": write_ability,
        "write_combatant": write_combatant,
        "write_encounter": write_encounter,
        "read_component": read_component,
        "read_node": read_node,
        "read_story_state": read_story_state,
        "generate_asset": generate_asset,
        "validate": validate_tool,
        "update_scratchpad": update_scratchpad,
        "request_review": request_review,
    }
