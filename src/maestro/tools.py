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


# Small local models routinely over-escape when emitting script text as a JSON string
# value — producing literal "\n" / "\"" instead of real newlines and quotes, which
# corrupts the Ren'Py node. Undo one level of over-escaping. Idempotent for correct
# content (real newlines/quotes are untouched).
def _normalize_script(text):
    if not isinstance(text, str):
        return text
    return text.replace("\\n", "\n").replace("\\t", "\t").replace('\\"', '"')


# Story-state delta fields, so write_node can accept them whether nested under
# story_state_delta or passed flat (the model does both).
_DELTA_FIELDS = ("new_facts", "entity_updates", "open_threads_add",
                 "open_threads_resolve", "event_summary")


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
        "description": "Write one story node into node_scripts AND merge its story-state "
                       "delta in the same call (dialogue + continuity bookkeeping together).",
        "parameters": {"type": "object", "properties": {
            "node_id": {"type": "string"},
            "content": {"description": "The node's Ren'Py script text"},
            "story_state_delta": {"type": "object", "description":
                "new_facts[], entity_updates{}, open_threads_add[], "
                "open_threads_resolve[], event_summary"},
        }, "required": ["node_id", "content"]}}},
    {"type": "function", "function": {
        "name": "edit_node",
        "description": "Surgically replace a snippet inside ONE existing node WITHOUT "
                       "rewriting the whole scene. Use this to fix a single broken line "
                       "(e.g. an unterminated string the linter flagged) — it preserves "
                       "the node's jumps/menus so you don't break reachability. Replaces "
                       "the first exact occurrence of `find`.",
        "parameters": {"type": "object", "properties": {
            "node_id": {"type": "string"},
            "find": {"type": "string",
                     "description": "exact text to replace; include enough to be unique in the node"},
            "replace": {"type": "string", "description": "replacement (may be empty to delete)"},
        }, "required": ["node_id", "find", "replace"]}}},
    {"type": "function", "function": {
        "name": "read_component",
        "description": "Read a component you previously wrote.",
        "parameters": {"type": "object", "properties": {
            "component_id": {"type": "string"}}, "required": ["component_id"]}}},
    {"type": "function", "function": {
        "name": "read_node",
        "description": "Read one node's current Ren'Py script — do this before edit_node so "
                       "you quote an EXACT snippet from it, and to see a node's jumps/lines "
                       "before fixing it.",
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
        # Fix over-escaped script text the model may have written into node_scripts.
        if component_id == "node_scripts" and isinstance(content.get("scripts"), dict):
            content["scripts"] = {k: _normalize_script(v) for k, v in content["scripts"].items()}
        state.write_component(component_id, content)
        return {"ok": True, "component_id": component_id}

    def write_node(node_id: str, content, story_state_delta: Optional[Dict] = None,
                   **delta_fields) -> Dict:
        """Fused: write one node into node_scripts AND merge its story-state delta.

        Producing the dialogue and the continuity bookkeeping in one call keeps them
        consistent. The next node reads the updated story state, never prior script.
        Tolerates the delta passed nested (story_state_delta) or as flat kwargs.
        """
        _require_frozen()
        if _locked("node_scripts"):
            return _locked_error("node_scripts")
        if node_id == "start":
            return {"ok": False, "error": "do not use 'start' as a node id — the builder "
                                          "adds 'label start' that jumps to the first node"}
        if not isinstance(content, str) or not content.strip():
            return {"ok": False, "error": "node content must be non-empty Ren'Py script text"}
        from maestro.story_state import init_story_state, apply_delta

        # Tolerate a malformed story_state_delta (the model sometimes passes a list/str).
        delta = dict(story_state_delta) if isinstance(story_state_delta, dict) else {}
        delta.update({k: v for k, v in delta_fields.items() if k in _DELTA_FIELDS})

        ns = state.read_component("node_scripts") or {"scripts": {}, "node_ids": []}
        ns.setdefault("scripts", {})[node_id] = _normalize_script(content)
        ns.setdefault("node_ids", [])
        if node_id not in ns["node_ids"]:
            ns["node_ids"].append(node_id)
        state.write_component("node_scripts", ns)

        if delta:
            ss = state.read_story_state() or init_story_state(spec.story_state_schema)
            apply_delta(ss, delta)
            state.write_story_state(ss)

        return {"ok": True, "node_id": node_id}

    def edit_node(node_id: str, find: str, replace: str = "") -> Dict:
        """Surgically patch ONE node: replace the first exact occurrence of `find`.

        Lets the agent fix a single bad line (e.g. an unterminated string the linter
        flagged on a specific node) without regenerating the whole scene — a full
        rewrite tends to drop the node's jump/menu and orphan downstream nodes.
        """
        _require_frozen()
        if _locked("node_scripts"):
            return _locked_error("node_scripts")
        ns = state.read_component("node_scripts") or {}
        scripts = ns.get("scripts", {})
        if node_id not in scripts:
            return {"ok": False, "error": f"no node {node_id!r} to edit"}
        text = scripts[node_id]
        if not isinstance(find, str) or not find or find not in text:
            return {"ok": False, "error":
                    f"`find` text not present in {node_id} — quote an exact snippet from the node"}
        scripts[node_id] = _normalize_script(
            text.replace(find, replace if isinstance(replace, str) else "", 1))
        state.write_component("node_scripts", ns)
        return {"ok": True, "node_id": node_id}

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
        scripts = (state.read_component("node_scripts") or {}).get("scripts", {})
        if node_id not in scripts:
            return {"ok": False, "error": f"no node {node_id!r}"}
        return {"ok": True, "node_id": node_id, "content": scripts[node_id]}

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
        "read_component": read_component,
        "read_node": read_node,
        "read_story_state": read_story_state,
        "generate_asset": generate_asset,
        "validate": validate_tool,
        "compile_renpy": compile_renpy_tool,
        "update_scratchpad": update_scratchpad,
        "request_review": request_review,
    }
