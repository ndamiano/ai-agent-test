"""Human-in-the-loop arbitration over a run — the human as arbiter of "done".

Two durable, human-only levers (no agent tools — the agent never calls these):
  - human todos: the human adds a to-do against a component, the build won't complete
    while one is open, and only the human marks it done.
  - waivers: the human accepts a machine check the validator still reports red, removing
    it from the to-do and from the completion gate.

Both live on disk (state.read/write_human_todos / _waivers), so the executor — which
rebuilds its context from durable state every step — picks them up with no plumbing.
"""

import json
import uuid
from typing import Dict, List

from maestro.validate import validate


def check_sig(failure: Dict) -> str:
    """Stable identity of a machine check on a component, independent of its (varying)
    detail text. Waiving keys off this."""
    return json.dumps(
        {"component_id": failure.get("component_id"), "check": failure.get("check", {})},
        sort_keys=True, ensure_ascii=False)


def _todo_as_failure(todo: Dict) -> Dict:
    """An open human todo, shaped like a validate failure so it merges into the one to-do."""
    return {
        "component_id": todo.get("component_id"),
        "check": {"type": "human_todo", "id": todo.get("id"), "text": todo.get("text", "")},
        "detail": todo.get("text", ""),
        "human": True,
    }


# ── human todos ───────────────────────────────────────────────────────────────
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


# ── waivers ─────────────────────────────────────────────────────────────────
def waive(state, component_id: str, check: Dict, note: str = "") -> Dict:
    sig = check_sig({"component_id": component_id, "check": check})
    waivers = state.read_waivers()
    if not any(w.get("sig") == sig for w in waivers):
        waivers.append({"sig": sig, "component_id": component_id, "check": check, "note": note})
        state.write_waivers(waivers)
    return {"sig": sig, "component_id": component_id, "check": check}


def unwaive(state, sig: str) -> bool:
    waivers = state.read_waivers()
    kept = [w for w in waivers if w.get("sig") != sig]
    state.write_waivers(kept)
    return len(kept) != len(waivers)


def waived_sigs(state) -> set:
    return {w.get("sig") for w in state.read_waivers()}


# ── the effective to-do (what the human and the completion gate actually see) ──
def machine_failures(spec, state) -> List[Dict]:
    """validate's failures minus any the human has waived."""
    waived = waived_sigs(state)
    return [f for f in validate(spec, state) if check_sig(f) not in waived]


def effective_failures(spec, state) -> List[Dict]:
    """The full human-facing to-do: unwaived machine failures + open human todos."""
    return machine_failures(spec, state) + [_todo_as_failure(t) for t in open_todos(state)]
