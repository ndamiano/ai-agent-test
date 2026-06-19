"""Artifact tools — the frequent, autonomous capabilities the executor dispatches.

Bound to one run's durable state. build_tools(spec, state) returns the {name: fn}
registry the Executor consumes; the agent chooses which to call and when.

The build tools (write_component, generate_asset) refuse to run until the spec is
frozen — the human gate is load-bearing. read_component, validate, compile_renpy
and update_scratchpad are safe before freezing.

State is bounded on purpose: there is no raw read_file/write_file. Components are
written by id; scratchpad is replaced, not appended.
"""

import re
from typing import Callable, Dict, List, Optional

from maestro.validate import validate


class SpecNotFrozen(RuntimeError):
    pass


# Small local models routinely over-escape when emitting script text as a JSON string
# value, and inconsistently so — a single intended newline can arrive as "\n", or as a
# backslash welded to a real newline ("\\\n"), or as several stacked backslashes; quotes
# likewise come back as "\"", "\\\"", etc. Collapsing one fixed level (the old behaviour)
# left the surplus backslashes behind, which broke labels/indentation and made every
# dialogue line unparseable. Collapse any run of backslashes before a newline or quote,
# and any run before a literal n/t escape. Idempotent for correct content.
_BSLASH_NEWLINE = re.compile(r"\\+\n")
_BSLASH_LIT_N = re.compile(r"\\+n")
_BSLASH_LIT_T = re.compile(r"\\+t")
_BSLASH_QUOTE = re.compile(r'\\+"')

# Models emit "smart" Unicode punctuation (curly quotes, em/en dashes) in script text, but
# then type ASCII when asking edit_node to `find` a snippet — so the find never matches the
# stored curly text and the edit loop spins. Fold to ASCII at write time so stored content
# matches what the model copies back. Length-preserving (1 char → 1 char) so edit_node can
# reuse it for offset-stable match against any content written before this fold existed.
_PUNCT_FOLD = str.maketrans({
    "‘": "'", "’": "'",      # ‘ ’ single curly quotes / apostrophe
    "“": '"', "”": '"',      # “ ” double curly quotes
    "–": "-", "—": "-",      # – — en / em dash
})

# *word* is markdown emphasis the model reaches for; Ren'Py's emphasis is {i}…{/i}. Convert
# paired runs (so the intent survives as italic) then drop any unpaired stray asterisk.
_EMPHASIS_RE = re.compile(r"\*([^*\n]+?)\*")


def _normalize_script(text):
    if not isinstance(text, str):
        return text
    text = _BSLASH_NEWLINE.sub("\n", text)
    text = _BSLASH_LIT_N.sub("\n", text)
    text = _BSLASH_LIT_T.sub("\t", text)
    text = _BSLASH_QUOTE.sub('"', text)
    text = text.translate(_PUNCT_FOLD)
    text = text.replace("…", "...")
    text = _EMPHASIS_RE.sub(r"{i}\1{/i}", text)
    text = text.replace("*", "")
    return text


def _flexible_find(text: str, find: str):
    """Locate `find` inside `text`, returning (start, end) into the ORIGINAL text or (-1, -1).

    edit_node's old exact-substring match was the top cycle-waster: a small model cannot
    reproduce a multi-line snippet's leading indentation and newlines byte-for-byte, nor the
    stored smart-punctuation, so the find missed and the edit loop spun for dozens of steps.
    Match in three widening passes, each offset-stable so the caller can splice the original:
      1. exact substring
      2. punctuation-folded (curly quotes/dashes → ASCII; the fold is 1:1 so offsets hold)
      3. whitespace-flexible: any run of spaces/tabs/newlines in `find` matches any run in
         `text` (so wrong indentation or space-vs-newline no longer defeats the match)
    """
    if not isinstance(find, str) or not find:
        return -1, -1
    i = text.find(find)
    if i >= 0:
        return i, i + len(find)
    ft, ff = text.translate(_PUNCT_FOLD), find.translate(_PUNCT_FOLD)  # 1:1 → indices map back
    i = ft.find(ff)
    if i >= 0:
        return i, i + len(ff)
    tokens = ff.split()
    if not tokens:
        return -1, -1
    m = re.search(r"\s+".join(re.escape(t) for t in tokens), ft)
    return (m.start(), m.end()) if m else (-1, -1)


# Story-state delta fields, so write_node can accept them whether nested under
# story_state_delta or passed flat (the model does both).
_DELTA_FIELDS = ("new_facts", "entity_updates", "open_threads_add",
                 "open_threads_resolve", "event_summary")


# A dialogue line is `<speaker> "..."`. A speaker that isn't a defined Character compiles
# to a NameError deep in the Ren'Py build — a failure the agent then chases for dozens of
# steps via edit_node. Catch it at write time instead. Valid speakers = premise character
# ids + the always-defined `act` narrator + Ren'Py statement keywords (mirror renpy/_script.py).
_SPEAKER_RE = re.compile(r'^[ \t]*(\w+)\s+"', re.MULTILINE)
_SPEAKER_KEYWORDS = {"scene", "show", "hide", "jump", "return", "menu", "call", "pause",
                     "play", "stop", "queue", "voice", "nvl", "window", "image", "define",
                     "transform", "init", "python", "label", "with", "extend", "act",
                     # control flow — common in hotspot logic (`if "key" in inventory:`), never speakers
                     "if", "elif", "else", "while"}


def _room_error(room_id: str, content: Dict) -> Optional[str]:
    """Reject a malformed single room up front (mirrors the schema validators) so a wrong
    shape steers immediately instead of crashing the pnc stitch later."""
    if not content.get("bg"):
        return f"room {room_id!r} needs a 'bg' (an asset_manifest background id)"
    hs = content.get("hotspots")
    if not isinstance(hs, list) or not hs:
        return f"room {room_id!r}.hotspots must be a non-empty list of hotspot objects"
    for j, h in enumerate(hs):
        if not isinstance(h, dict):
            return f"room {room_id!r}.hotspots[{j}] must be an object"
        for f in ("id", "label", "logic"):
            if not h.get(f):
                return f"room {room_id!r}.hotspots[{j}] needs a '{f}'"
        rect = h.get("rect")
        if not (isinstance(rect, list) and len(rect) == 4 and all(isinstance(n, (int, float)) for n in rect)):
            return f"room {room_id!r}.hotspots[{j}].rect must be [x, y, w, h] (4 numbers)"
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
        "name": "write_room",
        "description": "Write one room into the rooms component: its background plus the "
                       "clickable hotspots (each a screen rect + a Ren'Py logic body that "
                       "runs on click). Adds the room to room_ids. Point-and-click only.",
        "parameters": {"type": "object", "properties": {
            "room_id": {"type": "string", "description": "e.g. 'room_kitchen'"},
            "content": {"type": "object", "description":
                "{bg: <background id>, hotspots: [{id, rect:[x,y,w,h], label, logic}]}"},
        }, "required": ["room_id", "content"]}}},
    {"type": "function", "function": {
        "name": "edit_room",
        "description": "Surgically patch ONE hotspot's logic inside a room WITHOUT rewriting "
                       "it — fix a broken line or repoint a `jump`. Replaces the first "
                       "occurrence of `find`. Point-and-click only.",
        "parameters": {"type": "object", "properties": {
            "room_id": {"type": "string"},
            "hotspot_id": {"type": "string"},
            "find": {"type": "string", "description": "exact text to replace; enough to be unique"},
            "replace": {"type": "string", "description": "replacement (may be empty to delete)"},
        }, "required": ["room_id", "hotspot_id", "find", "replace"]}}},
    {"type": "function", "function": {
        "name": "read_room",
        "description": "Read one room's current background + hotspots (with their logic) — do "
                       "this before edit_room so you quote an exact snippet.",
        "parameters": {"type": "object", "properties": {
            "room_id": {"type": "string"}}, "required": ["room_id"]}}},
    {"type": "function", "function": {
        "name": "set_rooms_meta",
        "description": "Declare the point-and-click game's global scaffold: the win `goal`, the "
                       "inventory `items`, the puzzle `flags`, and the `start_room`. Required for "
                       "goal_reachable — write_room never sets these. Merges (pass only what changes).",
        "parameters": {"type": "object", "properties": {
            "goal": {"type": "object", "description":
                     "{type: 'flag'|'room', id: '<winning flag or room id>'}"},
            "items": {"type": "array", "items": {"type": "object"},
                      "description": "[{id, name, examine}] — ids match inventory.append(...) in hotspots"},
            "flags": {"type": "array", "items": {"type": "string"},
                      "description": "puzzle boolean names, e.g. ['door_unlocked', 'escaped']"},
            "start_room": {"type": "string"},
        }, "required": []}}},
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

    def _speaker_error(script: str) -> Optional[str]:
        """Reject a node whose dialogue names a speaker that is not a defined character —
        the cast is locked upstream, so an unknown speaker is a typo (e.g. 'elias_vanaka'
        for 'elias_voss') that only surfaces as a compile failure dozens of steps later."""
        prem = state.read_component("premise") or {}
        chars = {c.get("id") for c in prem.get("characters", []) if c.get("id")}
        if not chars:  # premise not authored yet — nothing to check against
            return None
        bad = sorted(set(_SPEAKER_RE.findall(script)) - chars - _SPEAKER_KEYWORDS)
        if not bad:
            return None
        return (f"undefined speaker(s) {bad} — not defined characters, they will break the "
                f"compile. Use ONLY these exact character ids: {sorted(chars)}. For narration, "
                f"write a plain \"...\" line with no speaker prefix.")

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
        # Same for a whole-rooms author: normalize each hotspot's Ren'Py logic body.
        if component_id == "rooms" and isinstance(content.get("rooms"), dict):
            for room in content["rooms"].values():
                for h in (room.get("hotspots", []) if isinstance(room, dict) else []):
                    if isinstance(h, dict) and isinstance(h.get("logic"), str):
                        h["logic"] = _normalize_script(h["logic"])
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
        script = _normalize_script(content)
        err = _speaker_error(script)
        if err:
            return {"ok": False, "error": err}
        from maestro.story_state import init_story_state, apply_delta

        # Tolerate a malformed story_state_delta (the model sometimes passes a list/str).
        delta = dict(story_state_delta) if isinstance(story_state_delta, dict) else {}
        delta.update({k: v for k, v in delta_fields.items() if k in _DELTA_FIELDS})

        ns = state.read_component("node_scripts") or {"scripts": {}, "node_ids": []}
        ns.setdefault("scripts", {})[node_id] = script
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
        start, end = _flexible_find(text, find)
        if start < 0:
            # Return the node verbatim so the next attempt copies a real snippet instead of
            # guessing again — the find-mismatch retry loop was the top cycle-waster. Match is
            # whitespace- and punctuation-flexible, so a SHORT distinctive snippet is enough.
            return {"ok": False, "error":
                    f"`find` text not present in {node_id} — copy a SHORT exact phrase from the "
                    f"node text below (one line is plenty; whitespace need not match):\n{text}"}
        rep = replace if isinstance(replace, str) else ""
        patched = _normalize_script(text[:start] + rep + text[end:])
        err = _speaker_error(patched)
        if err:
            return {"ok": False, "error": err}
        scripts[node_id] = patched
        state.write_component("node_scripts", ns)
        return {"ok": True, "node_id": node_id}

    def write_room(room_id: str, content) -> Dict:
        """Write one room (bg + hotspots) into the rooms component, mirroring write_node:
        the scaffold (start_room/items/flags/goal/room_ids) is laid by write_component first,
        then rooms are filled one at a time so each call stays focused."""
        _require_frozen()
        if _locked("rooms"):
            return _locked_error("rooms")
        if not isinstance(content, dict):
            return {"ok": False, "error": "room content must be a JSON object {bg, hotspots}"}
        err = _room_error(room_id, content)
        if err:
            return {"ok": False, "error": err}
        for h in content.get("hotspots", []):
            if isinstance(h.get("logic"), str):
                h["logic"] = _normalize_script(h["logic"])
        speaker_err = _speaker_error("\n".join(h.get("logic", "") for h in content["hotspots"]))
        if speaker_err:
            return {"ok": False, "error": speaker_err}

        rooms = state.read_component("rooms") or {"room_ids": [], "rooms": {}}
        rooms.setdefault("rooms", {})[room_id] = content
        rooms.setdefault("room_ids", [])
        if room_id not in rooms["room_ids"]:
            rooms["room_ids"].append(room_id)
        if not rooms.get("start_room"):
            rooms["start_room"] = room_id
        state.write_component("rooms", rooms)
        return {"ok": True, "room_id": room_id}

    def set_rooms_meta(goal=None, items=None, flags=None, start_room=None) -> Dict:
        """Declare the game's global scaffold on the rooms component — the win goal, inventory
        items, puzzle flags, start room. write_room builds individual rooms but never these, so
        without this the goal stays undeclared and goal_reachable can't pass. Merges: only the
        fields passed change."""
        _require_frozen()
        if _locked("rooms"):
            return _locked_error("rooms")
        if goal is not None and (not isinstance(goal, dict)
                                 or goal.get("type") not in ("flag", "room") or not goal.get("id")):
            return {"ok": False, "error":
                    "goal must be {\"type\": \"flag\" or \"room\", \"id\": \"<the winning flag or "
                    "room id>\"} — e.g. {\"type\": \"flag\", \"id\": \"escaped\"}"}
        rooms = state.read_component("rooms") or {"room_ids": [], "rooms": {}}
        if goal is not None:
            rooms["goal"] = goal
        if items is not None:
            rooms["items"] = items
        if flags is not None:
            rooms["flags"] = flags
        if start_room is not None:
            rooms["start_room"] = start_room
        state.write_component("rooms", rooms)
        return {"ok": True, "goal": rooms.get("goal"), "items": rooms.get("items"),
                "flags": rooms.get("flags"), "start_room": rooms.get("start_room")}

    def edit_room(room_id: str, hotspot_id: str, find: str, replace: str = "") -> Dict:
        """Surgically patch one hotspot's logic — fix a line or repoint a jump without
        rewriting the room (which tends to drop other hotspots' wiring)."""
        _require_frozen()
        if _locked("rooms"):
            return _locked_error("rooms")
        rooms = state.read_component("rooms") or {}
        room = (rooms.get("rooms") or {}).get(room_id)
        if room is None:
            return {"ok": False, "error": f"no room {room_id!r} to edit"}
        hs = next((h for h in room.get("hotspots", []) if h.get("id") == hotspot_id), None)
        if hs is None:
            return {"ok": False, "error": f"no hotspot {hotspot_id!r} in room {room_id!r}"}
        text = hs.get("logic", "")
        start, end = _flexible_find(text, find)
        if start < 0:
            return {"ok": False, "error":
                    f"could not find {find!r} in hotspot {hotspot_id!r}. Copy a snippet from its "
                    f"logic below (one line is plenty; whitespace need not match):\n{text}"}
        rep = replace if isinstance(replace, str) else ""
        patched = _normalize_script(text[:start] + rep + text[end:])
        speaker_err = _speaker_error(patched)
        if speaker_err:
            return {"ok": False, "error": speaker_err}
        hs["logic"] = patched
        state.write_component("rooms", rooms)
        return {"ok": True, "room_id": room_id, "hotspot_id": hotspot_id}

    def read_room(room_id: str) -> Dict:
        room = (state.read_component("rooms") or {}).get("rooms", {}).get(room_id)
        if room is None:
            return {"ok": False, "error": f"no room {room_id!r}"}
        return {"ok": True, "room_id": room_id, "content": room}

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
        "write_room": write_room,
        "edit_room": edit_room,
        "read_room": read_room,
        "set_rooms_meta": set_rooms_meta,
        "read_component": read_component,
        "read_node": read_node,
        "read_story_state": read_story_state,
        "generate_asset": generate_asset,
        "validate": validate_tool,
        "compile_renpy": compile_renpy_tool,
        "update_scratchpad": update_scratchpad,
        "request_review": request_review,
    }
