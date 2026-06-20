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


def _place_content_error(content) -> Optional[str]:
    if not isinstance(content, dict):
        return "place content must be a JSON object {kind, background, interactables}"
    inter = content.get("interactables")
    if not isinstance(inter, list) or not inter:
        return "place.interactables must be a non-empty list of clickable objects"
    for j, h in enumerate(inter):
        if not isinstance(h, dict) or not h.get("id"):
            return f"place.interactables[{j}] needs an 'id'"
        act = h.get("action")
        if not isinstance(act, dict) or not act.get("type"):
            return f"place.interactables[{j}].action needs an object with a 'type'"
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
                       "single line by index (text/speaker/effects) or replace the node's `end`. "
                       "Use to repoint a jump/menu target or fix one line.",
        "parameters": {"type": "object", "properties": {
            "node_id": {"type": "string"},
            "line_index": {"type": "integer", "description": "index into lines to patch (0-based)"},
            "text": {"type": "string", "description": "new text for that line"},
            "speaker": {"description": "new speaker id for that line (null for narration)"},
            "effects": {"type": "array", "items": {"type": "object"},
                        "description": "replace that line's effects"},
            "end": {"type": "object", "description": "replace the node's terminal end object"},
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
        "name": "compile_renpy",
        "description": "Build the artifact into a Ren'Py project and report the gate result.",
        "parameters": {"type": "object", "properties": {}, "required": []}}},
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

_PLACE_TOOLS = {"write_place", "edit_place", "read_place", "set_places_meta"}


def tool_schemas_for(genre: str) -> List[Dict]:
    """Tool schemas for the decider, scoped to the genre — point-and-click needs the place
    tools; the visual novel doesn't, so they're dropped to keep its tool set focused."""
    if genre == "point_and_click":
        return TOOL_SCHEMAS
    return [s for s in TOOL_SCHEMAS if s["function"]["name"] not in _PLACE_TOOLS]


def build_tools(spec, state, schemas: Optional[Dict[str, Callable]] = None) -> Dict[str, Callable]:
    # schemas: component_id -> validator(content) -> error str | None. Injected by the
    # caller (e.g. renpy) so maestro stays genre-agnostic. None = no structural checks.
    schemas = schemas or {}

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
    def write_component(component_id: str, content) -> Dict:
        _require_frozen()
        if _locked(component_id):
            return _locked_error(component_id)
        # Reject the wrong shape up front so it's an immediate steering signal, not a
        # crash inside compile later. The bad content is NOT persisted.
        err = _schema_error(component_id, content)
        if err:
            return {"ok": False, "error": f"invalid {component_id}: {err}"}
        state.write_component(component_id, content)
        return {"ok": True, "component_id": component_id}

    def write_node(node_id: str, content, story_state_delta: Optional[Dict] = None,
                   **delta_fields) -> Dict:
        """Fused: write one IR node into `nodes` AND merge its story-state delta.

        `content` is an IR node object ({lines, end}); escaping/rendering is the compiler's
        job. Producing the dialogue and the continuity bookkeeping in one call keeps them
        consistent — the next node reads the updated story state, never prior script.
        """
        _require_frozen()
        if _locked("nodes"):
            return _locked_error("nodes")
        if node_id == "start":
            return {"ok": False, "error": "do not use 'start' as a node id — the compiler "
                                          "adds 'label start' that jumps to the first node"}
        err = _node_content_error(content)
        if err:
            return {"ok": False, "error": err}
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
                  speaker=_UNSET, effects: Optional[List] = None, end: Optional[Dict] = None) -> Dict:
        """Patch ONE field of a node without rewriting it: a single line (by index) or the `end`.
        Repointing a jump/menu target or fixing one line, without disturbing the rest."""
        _require_frozen()
        if _locked("nodes"):
            return _locked_error("nodes")
        ns = state.read_component("nodes") or {}
        nodes = ns.get("nodes", {})
        if node_id not in nodes:
            return {"ok": False, "error": f"no node {node_id!r} to edit"}
        node = nodes[node_id]
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
                lines[line_index]["speaker"] = speaker
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

    def set_places_meta(goal=None, items=None, flags=None, variables=None, start_place=None) -> Dict:
        """Declare the point-and-click scaffold on `places` — win goal, items, flags, variables,
        start_place. write_place never sets these. Merges: only the fields passed change."""
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
            if not action.get("type"):
                return {"ok": False, "error": "action needs a 'type'"}
            h["action"] = action
        if position is not None:
            h["position"] = position
        if label is not None:
            h["label"] = label
        state.write_component("places", places)
        return {"ok": True, "place_id": place_id, "interactable_id": interactable_id}

    def read_place(place_id: str) -> Dict:
        place = (state.read_component("places") or {}).get("places", {}).get(place_id)
        if place is None:
            return {"ok": False, "error": f"no place {place_id!r}"}
        return {"ok": True, "place_id": place_id, "content": place}

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

    def compile_renpy_tool() -> Dict:
        # Lint-only during the loop; final packaging happens once at the end (run_build).
        from renpy.compiler import compile_renpy
        return compile_renpy(state.run_dir, distribute=False)

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
        "read_place": read_place,
        "set_places_meta": set_places_meta,
        "read_component": read_component,
        "read_node": read_node,
        "read_story_state": read_story_state,
        "generate_asset": generate_asset,
        "validate": validate_tool,
        "compile_renpy": compile_renpy_tool,
        "update_scratchpad": update_scratchpad,
        "request_review": request_review,
    }
