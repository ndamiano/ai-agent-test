"""The run-state FRAME every build step shares: spec, to-do, target, scratchpad, story state,
and the trailing result/stall nudge.

Nothing module-specific lives here. Each module is the one-stop shop for its own domain — it
owns its checks, its skeletons, its write-time policy, AND the block that presents its component
as context for other modules' prompts (e.g. `cast.character_cards`, `inventory.items_block`).
A consumer module composes its prompt from the sibling modules' blocks plus this frame.
"""

import json
from typing import Dict, List


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
