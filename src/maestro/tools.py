"""Artifact tools — the frequent, autonomous capabilities the executor dispatches.

Bound to one run's durable state. build_tools(spec, state) returns the {name: fn}
registry the Executor consumes; the agent chooses which to call and when.

The build tools (write_component, generate_asset) refuse to run until the spec is
frozen — the human gate is load-bearing. read_component, validate, compile_renpy
and update_scratchpad are safe before freezing.

State is bounded on purpose: there is no raw read_file/write_file. Components are
written by id; scratchpad is replaced, not appended.
"""

from typing import Callable, Dict, List, Optional

from maestro.ir_assemble import EMOTIONS, is_narration_speaker
from maestro.validate import validate


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


# The model intuitively writes a speaker STRING for narration ("narration"/"narrator") instead of
# the convention speaker:null. Left alone it isn't a declared character, so it only blows up far
# later at crossref/compile — where a small model thrashes trying to "fix" it. Normalize at write
# time so the intent (narration) is honored and the error never forms (is_narration_speaker is the
# shared rule, also applied as a backstop in ir_assemble).
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
    return None


_CARD_MODELS = {"high_card", "blackjack"}


def _match_content_error(content) -> Optional[str]:
    if not isinstance(content, dict):
        return "match content must be a JSON object {card_model, opponent, ante}"
    if content.get("card_model") not in _CARD_MODELS:
        return f"match.card_model must be one of {sorted(_CARD_MODELS)}"
    if not content.get("opponent"):
        return "match needs an 'opponent' (a premise character id)"
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
            "component_id": {"type": "string", "description": "Component id, e.g. 'premise'"},
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
        "description": "Write one place (room/map) into `places`: its background plus clickable "
                       "interactables (each a screen rect + a structured `action`). Adds it to "
                       "place_ids. Point-and-click only.",
        "parameters": {"type": "object", "properties": {
            "place_id": {"type": "string", "description": "e.g. 'room_kitchen'"},
            "content": {"type": "object", "description":
                "{kind: 'room', background: <asset id>, interactables: [{id, label, "
                "position:{rect:{x,y,w,h}}, action:{type, ...}}]}"},
        }, "required": ["place_id", "content"]}}},
    {"type": "function", "function": {
        "name": "edit_place",
        "description": "Patch ONE interactable in a place without rewriting it: replace its "
                       "`action` (e.g. repoint a move target / fix a use clause), `position`, or "
                       "`label`. Point-and-click only.",
        "parameters": {"type": "object", "properties": {
            "place_id": {"type": "string"},
            "interactable_id": {"type": "string"},
            "action": {"type": "object", "description": "replacement structured action"},
            "position": {"type": "object", "description": "replacement {rect:{x,y,w,h}}"},
            "label": {"type": "string"},
        }, "required": ["place_id", "interactable_id"]}}},
    {"type": "function", "function": {
        "name": "add_interactable",
        "description": "APPEND one new interactable (hotspot) to an existing place — without "
                       "rewriting it (write_place clobbers the others) and without repointing an "
                       "existing hotspot (edit_place breaks that hotspot's route). The right tool "
                       "to add a move hotspot for an unreachable place, or a use/win hotspot to set "
                       "the goal flag. Point-and-click only.",
        "parameters": {"type": "object", "properties": {
            "place_id": {"type": "string", "description": "the existing place to add to"},
            "interactable": {"type": "object", "description":
                "{id, label, position:{rect:{x,y,w,h}}, action:{type, ...}} — a new hotspot; its "
                "id must not already exist in the place"},
        }, "required": ["place_id", "interactable"]}}},
    {"type": "function", "function": {
        "name": "read_place",
        "description": "Read one place's current background + interactables (with their actions).",
        "parameters": {"type": "object", "properties": {
            "place_id": {"type": "string"}}, "required": ["place_id"]}}},
    {"type": "function", "function": {
        "name": "set_places_meta",
        "description": "Declare the point-and-click game's global scaffold on `places`: the win "
                       "`goal`, inventory `items`, puzzle `flags`, numeric `variables`, and "
                       "`start_place`. Required for goal_reachable. Merges (pass only what changes).",
        "parameters": {"type": "object", "properties": {
            "goal": {"type": "object", "description":
                     "{type: 'flag'|'room', id: '<winning flag or place id>'}"},
            "items": {"type": "array", "items": {"type": "object"},
                      "description": "[{id, name, examine}] — ids match take/use actions"},
            "flags": {"type": "array", "items": {"type": "string"},
                      "description": "puzzle boolean names, e.g. ['door_open', 'escaped']"},
            "variables": {"type": "array", "items": {"type": "object"},
                          "description": "[{id, default}] numeric state"},
            "start_place": {"type": "string"},
        }, "required": []}}},
    {"type": "function", "function": {
        "name": "write_match",
        "description": "Write one wagering card match into `matches`: its card_model (high_card or "
                       "blackjack), opponent (a premise character id), ante {var, amount}, and "
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

def tool_schemas_for(spec) -> List[Dict]:
    """Tool schemas for the decider, scoped to the spec's active modules. A tool a module *owns*
    (e.g. navigation's place tools) is included only when that module is in the composition; every
    ungated tool is always present. Keeps each game's tool set focused on what it can build.

    `spec` may be a Spec, a spec dict, or a bare genre/preset string."""
    from maestro.modules import compose, modules_for, MODULE_REGISTRY

    spec_data = getattr(spec, "data", spec)
    if isinstance(spec_data, str):
        spec_data = {"genre": spec_data}

    gated = {name for m in MODULE_REGISTRY.values() for name in m.tool_names}
    active = set(compose(modules_for(spec_data)).tool_names)
    return [s for s in TOOL_SCHEMAS
            if s["function"]["name"] not in gated or s["function"]["name"] in active]


def build_tools(spec, state, schemas: Optional[Dict[str, Callable]] = None) -> Dict[str, Callable]:
    # schemas: component_id -> validator(content) -> error str | None. Injected by the
    # caller (e.g. renpy) so maestro stays genre-agnostic. None = no structural checks.
    schemas = schemas or {}

    # Born-compliant nodes: write_node enforces the spec's each_node_min_lines floor, so a thin
    # node is rejected at creation instead of passing `count` as a stub and then dragging the
    # small model through a whack-a-mole each_node_min_lines repair phase (its worst failure mode).
    _node_min_lines = next(
        (dc.get("min", 0)
         for c in spec.components if c.get("id") == "nodes"
         for dc in c.get("done_conditions", []) if dc.get("type") == "each_node_min_lines"), 0)

    # A component LOCKS once its own done-conditions all pass: full rewrites would
    # otherwise drop/rename ids that other components already reference, regressing
    # previously-passing checks. EXCEPTION: a component whose "done" includes a
    # `compiles` check is the terminal/integration piece (e.g. node_scripts) — its
    # correctness depends on the whole artifact, so it must stay writable until the
    # build ends, and we never run its expensive compile just to test a lock.
    # Keying on the compiles check (not agent-declared deps) is robust to specs that
    # leave deps empty.
    def _has_compiles(c: Dict) -> bool:
        return any(dc.get("type") == "compiles" for dc in c.get("done_conditions", []))
    # Lockable = has a real contract (done_conditions) and is not the compiles-gated
    # terminal component. A component with no contract never locks (it "passes" vacuously).
    _lockable = {c["id"] for c in spec.components
                 if c.get("done_conditions") and not _has_compiles(c)}

    def _require_frozen():
        if not spec.frozen:
            raise SpecNotFrozen("spec must be frozen before building the artifact")

    def _locked(component_id: str) -> bool:
        return (component_id in _lockable
                and state.read_component(component_id) is not None
                and not validate(spec, state, component_id))

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
        # Reject the wrong shape up front so it's an immediate steering signal, not a
        # crash inside compile later. The bad content is NOT persisted.
        err = _schema_error(component_id, content)
        if err:
            return {"ok": False, "error": f"invalid {component_id}: {err}"}
        state.write_component(component_id, content)
        return {"ok": True, "component_id": component_id}

    def write_node(node_id: str, content, story_state_delta: Optional[Dict] = None,
                   force: bool = False, **delta_fields) -> Dict:
        """Fused: write one IR node into `nodes` AND merge its story-state delta.

        `content` is an IR node object ({lines, end}); escaping/rendering is the compiler's
        job. Producing the dialogue and the continuity bookkeeping in one call keeps them
        consistent — the next node reads the updated story state, never prior script.
        force: a human-driven rewrite may overwrite a locked nodes component (override).
        """
        _require_frozen()
        if not force and _locked("nodes"):
            return _locked_error("nodes")
        if node_id == "start":
            return {"ok": False, "error": "do not use 'start' as a node id — the compiler "
                                          "adds 'label start' that jumps to the first node"}
        err = _node_content_error(content)
        if err:
            return {"ok": False, "error": err}
        _normalize_narration(content)
        if len(content["lines"]) < _node_min_lines:
            return {"ok": False, "error":
                    f"a node needs at least {_node_min_lines} lines/beats — this has "
                    f"{len(content['lines'])}. Write the FULL scene now (several dialogue beats "
                    f"with subtext), not a stub; thin nodes are rejected."}
        from maestro.story_state import init_story_state, apply_delta

        # Tolerate a malformed story_state_delta (the model sometimes passes a list/str).
        delta = dict(story_state_delta) if isinstance(story_state_delta, dict) else {}
        delta.update({k: v for k, v in delta_fields.items() if k in _DELTA_FIELDS})

        ns = state.read_component("nodes") or {"nodes": {}, "node_ids": []}
        ns.setdefault("nodes", {})[node_id] = content
        ns.setdefault("node_ids", [])
        if node_id not in ns["node_ids"]:
            ns["node_ids"].append(node_id)
        state.write_component("nodes", ns)

        if delta:
            ss = state.read_story_state() or init_story_state(spec.story_state_schema)
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

    def set_places_meta(goal=None, items=None, flags=None, variables=None, start_place=None,
                        **ignored) -> Dict:
        """Declare the point-and-click scaffold on `places` — win goal, items, flags, variables,
        start_place. write_place never sets these. Merges: only the fields passed change. Stray
        kwargs (e.g. the model jamming `nodes=` here) are ignored, not a crash — but note them so
        the model learns this tool can't touch that."""
        if ignored:
            return {"ok": False, "error":
                    f"set_places_meta does not take {sorted(ignored)} — it only declares goal/items/"
                    f"flags/variables/start_place. To change nodes use write_node/edit_node (in the "
                    f"nodes step); for hotspots use add_interactable/edit_place."}
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
        if items is not None:
            places["items"] = items
        if flags is not None:
            places["flags"] = flags
        if variables is not None:
            places["variables"] = variables
        if start_place is not None:
            places["start_place"] = start_place
        state.write_component("places", places)
        return {"ok": True, "goal": places.get("goal"), "items": places.get("items"),
                "flags": places.get("flags"), "start_place": places.get("start_place")}

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
        return {"ok": True, "story_state": state.read_story_state() or init_story_state(spec.story_state_schema)}

    def validate_tool(component_id: Optional[str] = None) -> Dict:
        failures = validate(spec, state, component_id)
        return {"ok": not failures, "failures": failures}

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
        "read_component": read_component,
        "read_node": read_node,
        "read_story_state": read_story_state,
        "generate_asset": generate_asset,
        "validate": validate_tool,
        "update_scratchpad": update_scratchpad,
        "request_review": request_review,
    }
