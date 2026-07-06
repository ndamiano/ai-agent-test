"""human — the human-in-the-loop, wearing the Module interface so its work rides the same loop.

Always composed. Two durable, human-only levers (no agent tool touches them):
  - todos:   a note the human adds against a component; emitted as an ErrorType.HUMAN that outranks
             every build/fix error, so it preempts mid-build and is addressed next step.
  - waivers: a build/fix error the human accepts as good-enough; its durable key is subtracted from
             the loop's to-do. Keyed on Error.identity(), so it auto-expires when the situation changes.

The module-level functions (add_todo / resolve_todo / waive / ...) are the API the games router calls.

Example games:
  - "a two-hander visual novel"      — human + cast + story + scenes
  - "a point-and-click escape room"  — human + world + inventory
"""

import uuid
from typing import Dict, List, Set, Tuple

from maestro.modules.module import load_prompt
from maestro.modules.module import (
    Check,
    CorrectionPrompt,
    Error,
    ErrorType,
    Module,
    idkey,
    register_module,
)

# Broad on purpose: a human note may target any component.
_HUMAN_TOOLS: Tuple[str, ...] = (
    "write_component", "write_node", "edit_node", "write_place", "edit_place",
    "add_interactable", "set_places_meta", "write_match", "edit_match", "generate_asset",
    "read_component", "read_node", "read_place", "update_scratchpad",
)


# ── durable todo / waiver store (on RunState) ────────────────────────────────
def add_todo(state, component_id: str, text: str) -> Dict:
    todo = {"id": uuid.uuid4().hex[:8], "component_id": component_id, "text": text, "done": False}
    todos = state.read_human_todos()
    todos.append(todo)
    state.write_human_todos(todos)
    return todo


def resolve_todo(state, todo_id: str, done: bool = True) -> bool:
    todos = state.read_human_todos()
    hit = False
    for t in todos:
        if t.get("id") == todo_id:
            t["done"] = done
            hit = True
    state.write_human_todos(todos)
    return hit


def open_todos(state) -> List[Dict]:
    return [t for t in state.read_human_todos() if not t.get("done")]


def waive(state, error_idkey: str, note: str = "") -> Dict:
    waivers = state.read_waivers()
    if not any(w.get("idkey") == error_idkey for w in waivers):
        waivers.append({"idkey": error_idkey, "note": note})
        state.write_waivers(waivers)
    return {"idkey": error_idkey, "note": note}


def unwaive(state, error_idkey: str) -> bool:
    waivers = state.read_waivers()
    kept = [w for w in waivers if w.get("idkey") != error_idkey]
    state.write_waivers(kept)
    return len(kept) != len(waivers)


def waived_idkeys(state) -> Set[str]:
    return {w.get("idkey") for w in state.read_waivers()}


# ── durable dirty store (asset idkeys + review notes; same shape as waivers) ──
# An asset idkey is "<component>:<item_id>" — the on-disk component plus the id within it
# (nodes:scene_3, characters:mara, places:zone_crypt, combat:firebolt). It is NOT an Error
# identity (waivers use those); it names a buildable asset. A dirty entry EMITS an error the
# loop drains by rewriting (review_note = the instruction); a human clears it by thumbs-up.
def asset_idkey(component: str, item_id: str) -> str:
    return f"{component}:{item_id}"


def split_idkey(asset_key: str) -> Tuple[str, str]:
    component, _, item_id = (asset_key or "").partition(":")
    return component, item_id


def set_dirty(state, asset_key: str, note: str = "") -> Dict:
    """Flag an asset dirty (or update its note if already flagged). Thumbs-down and a manual
    'needs another look' are the same call."""
    dirty = state.read_dirty()
    for d in dirty:
        if d.get("idkey") == asset_key:
            d["note"] = note
            state.write_dirty(dirty)
            return d
    entry = {"idkey": asset_key, "note": note}
    dirty.append(entry)
    state.write_dirty(dirty)
    return entry


def clear_dirty(state, asset_key: str) -> bool:
    """Clear an asset's dirty flag — thumbs-up, or a completed rewrite. Returns whether one was
    removed."""
    dirty = state.read_dirty()
    kept = [d for d in dirty if d.get("idkey") != asset_key]
    state.write_dirty(kept)
    return len(kept) != len(dirty)


def dirty_entries(state) -> List[Dict]:
    return state.read_dirty()


def effective_failures(spec: Dict, state) -> List[Dict]:
    """The human-facing to-do: the effective errors (collected − waived, human todos included) as
    serializable dicts. Each carries `idkey` — the durable key the waive endpoint takes back."""
    from maestro.modules import compose
    from maestro.agent_loop import effective_pairs
    from maestro.modules.context import build_context
    modules = compose(spec.get("modules", []))
    ctx = build_context(spec, state)
    # `path` carries the human-todo id for a HUMAN error, so the UI can resolve it.
    return [{"component": e.component, "code": e.code, "type": e.type.value,
             "detail": e.message, "idkey": idkey(e), "path": e.path}
            for _, e in effective_pairs(modules, ctx)]


def _d_human_todo(chk, m, context):
    return [
        Error(type=ErrorType.HUMAN, code="human_todo", component=t.get("component_id") or "",
              message=t.get("text", ""), path=t.get("id"))
        for t in open_todos(context.state)
    ]


def _human_prompt(m, context, error: Error) -> CorrectionPrompt:
    import json
    from maestro import context_render as cr
    from maestro.modules import cast as cast_mod, inventory, scenes, world
    system = load_prompt("human_edit.txt")
    art = context.artifact
    # The note usually targets ONE component: show that component in full, everything else as
    # crafted indexes (the note may reference a scene, an item, a place by name).
    target_content = art.get(error.component) if error.component else None
    lines = [
        f"HUMAN DIRECTION (do exactly this): {error.message}",
        f"TARGET COMPONENT: {error.component or '(any)'}",
    ]
    # nodes/places grow unbounded — their index is below and the fixer has read tools.
    if target_content is not None and error.component not in ("nodes", "places"):
        lines += ["", f"CURRENT {error.component} CONTENT:",
                  json.dumps(target_content, ensure_ascii=False)]
    lines += cast_mod.character_cards(art) if error.component != "characters" else []
    lines += scenes.nodes_index_block(art) if error.component != "nodes" else []
    lines += world.places_index_block(art) if error.component != "places" else []
    lines += inventory.items_block(art) if error.component != "items" else []
    # Without the tail the read-then-edit flow is blind: the model reads a node on step 1 and
    # never sees the payload on step 2, looping reads until escalation forces a blind write.
    lines += cr.tail_block({"last_read": context.last_read, "last_result": context.last_result,
                            "stalled": context.stalled})
    lines += ["", f"Now make exactly this change: {error.message}",
              "One tool call, the smallest edit that does it (edit_node/edit_place patching one "
              "field beats write_node/write_place rewriting the whole thing). Tool call only, "
              "not prose."]
    return CorrectionPrompt(system=system, user="\n".join(lines), allowed_tools=_HUMAN_TOOLS)


_READONLY = {"read_component", "read_node", "read_place", "update_scratchpad"}


def _run_todo_fix(module, context, error, slot, services, dispatch):
    """One prompt step, but a successful mutating call marks the todo applied — otherwise the
    HUMAN error (which outranks everything) re-emits every sweep and the same direction is
    applied again and again. The human can reopen it from the panel if the edit missed."""
    def marking(name, args):
        result = dispatch(name, args)
        if name not in _READONLY and isinstance(result, dict) and result.get("ok"):
            resolve_todo(services.state, error.path)
        return result
    services.run(module.get_correction_prompt(context, error, slot=slot), dispatch=marking)


_DIRTY_DEFAULT_NOTE = "review this asset and improve it"


def _d_dirty(chk, m, context):
    """One HUMAN error per dirty asset — the human flagged it (thumbs-down / manual). The loop
    drains it by rewriting (note = instruction); a thumbs-up clears the flag and the error with it.
    Component + item id are recovered from the asset idkey, so the fix routes to the right path."""
    out = []
    for d in dirty_entries(context.state):
        component, item_id = split_idkey(d.get("idkey", ""))
        out.append(Error(type=ErrorType.HUMAN, code="dirty_asset", component=component,
                         message=d.get("note") or _DIRTY_DEFAULT_NOTE, path=item_id))
    return out


def _run_dirty_fix(module, context, error, slot, services, dispatch):
    """Rewrite the flagged asset from its review note, then clear the flag so the error retires.
    A dialogue node reuses `rewrite_node` (the per-scene note-driven regenerator); any other
    component takes the general human-direction edit step, clearing on the first successful
    mutating call (the human re-flags if the edit missed — same contract as a todo)."""
    key = asset_idkey(error.component, error.path)
    note = error.message
    if error.component == "nodes" and error.path:
        from maestro.rewrite import rewrite_node
        result = rewrite_node(services.spec, services.state, error.path, note, services.tools,
                              connector=services.conn, report=services._report)
        if result.get("ok"):
            clear_dirty(services.state, key)
        return

    def marking(name, args):
        result = dispatch(name, args)
        if name not in _READONLY and isinstance(result, dict) and result.get("ok"):
            clear_dirty(services.state, key)
        return result
    services.run(module.get_correction_prompt(context, error, slot=slot), dispatch=marking)


class Human(Module):
    id = "human"
    selectable = False   # always-on: the human-in-the-loop channel is never optional
    priority = 0   # irrelevant to ordering (HUMAN type ranks first), but explicit

    checks = [
        Check("human_todo", _d_human_todo, tier=ErrorType.HUMAN, build_prompt=_human_prompt,
              run=_run_todo_fix),
        Check("dirty_asset", _d_dirty, tier=ErrorType.HUMAN, build_prompt=_human_prompt,
              run=_run_dirty_fix),
    ]


MODULE = Human()
register_module(MODULE)
