"""human — the human-in-the-loop, wearing the Module interface so its work rides the same loop.

Always composed in. Two durable, human-only levers live on disk (no agent tools touch them):
  - todos: the human adds a to-do against a component; `get_errors` emits each as ErrorType.HUMAN,
    which outranks every build/fix error — so a todo preempts mid-build and is addressed next step.
    `get_correction_prompt` wraps the note in a tunable prompt + broad edit tools (the human can
    touch any component), so the loop never routes a human note into another module's narrow prompt.
  - waivers: the human accepts a machine (build/fix) error as good-enough; `waived_idkeys` returns
    their durable keys and the loop subtracts them. So the human's full delta is +todos / −waivers.
    A waiver is keyed on Error.identity(), so it auto-expires when the situation changes.

The module-level functions (add_todo / resolve_todo / open_todos / waive / unwaive / ...) are the
API surface the games router calls; they read/write the same durable state the loop reads.
"""

import uuid
from typing import Dict, List, Set, Tuple

from maestro.modules import context as ctxmod
from maestro.modules.module import load_prompt, skeleton_guide
from maestro.modules.module import (
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


class Human(Module):
    id = "human"
    selectable = False   # always-on: the human-in-the-loop channel is never optional
    priority = 0   # irrelevant to ordering (HUMAN type ranks first), but explicit

    def get_errors(self, context) -> List[Error]:
        return [
            Error(type=ErrorType.HUMAN, code="human_todo", component=t.get("component_id") or "",
                  message=t.get("text", ""), path=t.get("id"))
            for t in open_todos(context.state)
        ]

    def get_correction_prompt(self, context, error: Error) -> CorrectionPrompt:
        rd = ctxmod.render_dict(context, active=error.component or None, target=error,
                                upstream_views=getattr(context, "upstream_views", {}),
                                available_tools=_HUMAN_TOOLS)
        system = load_prompt("human_edit.txt")
        user = "\n".join([
            f"HUMAN DIRECTION (do exactly this): {error.message}",
            f"TARGET COMPONENT: {error.component or '(any)'}",
            "",
            "Make the change with one tool call. Tool call only, not prose.",
            "",
            f"CONTEXT: {rd}",
        ])
        return CorrectionPrompt(system=system, user=user, allowed_tools=_HUMAN_TOOLS)


MODULE = Human()
register_module(MODULE)
