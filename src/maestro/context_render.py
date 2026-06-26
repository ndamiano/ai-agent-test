"""Leaf helpers for rendering a build step's context.

There is NO shared context frame: each owning Module defines its own `render_context(ctx)` and
composes the blocks it wants, in the order it wants. Trimming what a mode sends to the model
touches only that module — no central function to special-case. These are the formatting and
graph-math primitives those renderers draw from (a todo line looks the same everywhere; the slot
math is the same wherever it runs), not policy about which blocks a mode carries.
"""

import json
from typing import Dict, List, Optional


def spec_block(ctx: Dict) -> List[str]:
    """The frozen spec: title, request, the resolved sizing `params`, and modules. (No per-component
    done-conditions any more — the TO-DO names every failing check, and the checks live in code.)"""
    return [f"SPEC: {json.dumps(ctx.get('spec', {}) or {}, ensure_ascii=False)}"]


def todo_block(todo: List) -> List[str]:
    """`todo` is a list of Error objects (the effective failures)."""
    lines = [f"- [{e.component}] {e.code}: {e.message}" for e in todo] \
        or ["(none — build may be complete)"]
    return ["TO-DO (failing done-conditions):", *lines]


def target_block(ctx: Dict) -> List[str]:
    target = ctx.get("target")
    if not target:
        return []
    return [
        "",
        f"YOUR TARGET — finish ONLY when THIS check passes: "
        f"[{target.component}] {target.code}: {target.message}",
        "Make the change that clears it. Don't chase other to-do items.",
    ]


def scratchpad_block(ctx: Dict) -> List[str]:
    return ["", f"SCRATCHPAD: {json.dumps(ctx.get('scratchpad', {}) or {}, ensure_ascii=False)}"]


def upstream_block(upstream: Dict) -> List[str]:
    if not upstream:
        return []
    return [
        "",
        "LOCKED COMPONENTS (settled — use these EXACT ids, do not invent or rename):",
        json.dumps(upstream, ensure_ascii=False),
    ]


def story_state_block(ctx: Dict) -> List[str]:
    if not ctx.get("story_state"):
        return []
    return [f"STORY STATE: {json.dumps(ctx['story_state'], ensure_ascii=False)}"]


def tail_block(ctx: Dict) -> List[str]:
    """The trailing run-state every step shows: last read payload, last result, and the stall
    nudge (the model just re-read without changing anything — make it act)."""
    out: List[str] = []
    if ctx.get("last_read"):
        out += ["", f"LAST READ:\n{ctx['last_read']}"]
    out += [f"LAST RESULT: {ctx.get('last_result')}"]
    if ctx.get("stalled"):
        out += [
            "",
            "⚠ You just repeated a read without changing anything. STOP reading — you have "
            "the content above. Call a write/edit tool NOW to make a change.",
        ]
    return out


# ── node-graph slot math (shared: scenes' renderer + services._create_guard) ───────────────────
def pick_slot(view: Dict) -> Optional[Dict]:
    """The system — not the author — chooses which scene to write next: the open slot whose beat
    comes earliest in the story, so the spine is built in dramatic order. None when there are no
    open slots (the entry node, or a fresh branch root is needed)."""
    slots = view.get("open_slots") or []
    if not slots:
        return None
    order = {b["id"]: i for i, b in enumerate(view.get("beats") or []) if b.get("id")}
    last = len(order)
    return sorted(slots, key=lambda s: (order.get(s.get("beat"), last), s["id"]))[0]


def beat_for_new_node(view: Dict, chosen: Optional[Dict], has_existing: bool) -> Optional[str]:
    """The beat the system stamps on the node being written — it picked the slot, so it owns the
    beat too (the author no longer guesses it). The assigned slot's beat; the first beat for the
    opening node; the first still-unrealized beat for an escape-hatch branch root. None (e.g. an
    ending slot, or a nodes build with no story) → leave the node's beat unset."""
    if chosen is not None:
        return chosen.get("beat")
    beat_ids = [b["id"] for b in (view.get("beats") or []) if b.get("id")]
    if not has_existing:
        return beat_ids[0] if beat_ids else None
    todo = view.get("beats_todo") or []
    return todo[0] if todo else None


def render_beat(b: Dict) -> str:
    stake = f' (stake: {b["tension"]})' if b.get("tension") else ""
    return f'{b.get("id")} — {b.get("summary", "")}{stake}'
