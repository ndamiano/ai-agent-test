"""Leaf helpers for rendering a build step's context.

There is NO shared context frame: each owning Module defines its own `render_context(ctx)` and
composes the blocks it wants, in the order it wants. Trimming what a mode sends to the model
touches only that module — no central function to special-case. These are the formatting and
graph-math primitives those renderers draw from (a todo line looks the same everywhere; the slot
math is the same wherever it runs), not policy about which blocks a mode carries.
"""

import json
from typing import Dict, List, Optional


def scoped_spec(ctx: Dict) -> Dict:
    """The spec, trimmed to what THIS step needs: the active component's full entry (its
    done_conditions are the bar it's working to) plus id+description for the rest. The other
    components' done_conditions are noise here — the TO-DO already names every failing check."""
    spec = ctx.get("spec", {}) or {}
    mode = ctx.get("mode")
    comps = []
    for c in spec.get("components", []):
        if c.get("id") == mode:
            comps.append(c)
        else:
            comps.append({"id": c.get("id"), "description": c.get("description", "")})
    return {**{k: v for k, v in spec.items() if k != "components"}, "components": comps}


def spec_block(ctx: Dict) -> List[str]:
    return [f"SPEC: {json.dumps(scoped_spec(ctx), ensure_ascii=False)}"]


def todo_block(todo: List[Dict]) -> List[str]:
    lines = [f"- [{f['component_id']}] {f['check'].get('type')}: {f.get('detail')}"
             for f in todo] or ["(none — build may be complete)"]
    return ["TO-DO (failing done-conditions):", *lines]


def target_block(ctx: Dict) -> List[str]:
    target = ctx.get("target")
    if not target:
        return []
    return [
        "",
        f"YOUR TARGET — finish ONLY when THIS check passes: "
        f"[{target.get('component_id')}] {target['check'].get('type')}: {target.get('detail')}",
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


# ── node-graph slot math (shared: dialogue's renderer + agent._create_guard) ───────────────────
def pick_slot(view: Dict) -> Optional[Dict]:
    """The system — not the author — chooses which scene to write next: the open slot whose beat
    comes earliest in the outline, so the spine is built in dramatic order. None when there are no
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
    ending slot, or a non-VN nodes build with no outline) → leave the node's beat unset."""
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
