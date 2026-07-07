"""The run-state FRAME plus the per-error payload archetypes every build step shares.

Nothing module-specific lives in the leaf blocks. Each module is the one-stop shop for its own
domain — it owns its checks, its skeletons, its write-time policy, AND the block that presents its
component as context for other modules' prompts (e.g. `cast.character_cards`, `inventory.items_block`).

The core rule: a fix ships ONLY what its check needs — never a whole-spec or whole-story-state dump.
An AUTHORING check gets its module's crafted `render_context` (upstream prose the author needs). A
REPAIR check points `Check.context` at one of the archetypes here:
  - `ctx_structural` — dedup/field/graph repair on the module's OWN component: target + a compact
    self-view + run-state. No cross-module prose.
  - `ctx_crossref` — a dangling reference or compile failure: target + the valid-id catalogues the
    ref must resolve into (ids only) + run-state. No prose.
"""

import json
from typing import Dict, List


# ── run-state frame (no dumps) ───────────────────────────────────────────────

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
        f"YOUR TARGET — finish ONLY when THIS check passes: "
        f"[{target.component}] {target.code}: {target.message}",
        "Make the change that clears it. Don't chase other to-do items.",
    ]


def premise_block(ctx: Dict) -> List[str]:
    """Title + concept + request — the story premise an AUTHORING call needs to invent content that
    fits. Replaces the whole-spec dump (module_reasons/engine/substrate/params were pure noise to
    the model)."""
    spec = ctx.get("spec", {}) or {}
    out = [f"TITLE: {spec.get('title', '')}"]
    if spec.get("concept"):
        out += ["", f"CONCEPT: {spec['concept']}"]
    if spec.get("request"):
        out += ["", f"REQUEST: {spec['request']}"]
    return out


def story_tail_block(ctx: Dict) -> List[str]:
    """The LIVE continuity an authoring call must respect — the recent-events tail and open threads
    only. NOT the whole cumulative story_state (established_facts grows unbounded; the spec's
    story_state_schema already seeded it — dumping both is the redundancy we're killing)."""
    ss = ctx.get("story_state") or {}
    if not ss:
        return []
    out: List[str] = []
    tail = ss.get("recent_events_tail") or []
    if tail:
        out += ["", "STORY SO FAR (most recent):", *(f"  - {e}" for e in tail)]
    threads = ss.get("open_threads") or []
    if threads:
        out += ["", "OPEN THREADS:", *(f"  - {t}" for t in threads)]
    return out


def tail_block(ctx: Dict) -> List[str]:
    """The trailing run-state every step shows: last read payload, last result, and the stall
    nudge (the model just re-read without changing anything — make it act)."""
    out: List[str] = []
    if ctx.get("last_read"):
        out += ["", f"LAST READ:\n{ctx['last_read']}"]
    out += ["", f"LAST RESULT: {ctx.get('last_result')}"]
    if ctx.get("stalled"):
        out += [
            "",
            "⚠ You just repeated a read without changing anything. STOP reading — you have "
            "the content above. Call a write/edit tool NOW to make a change.",
        ]
    return out


def self_digest_block(module, ctx: Dict) -> List[str]:
    """Delegate to the module's compact self-view (its own component, id-level)."""
    return module.self_digest(ctx.get("artifact") or {})


# ── valid-id catalogues (ids only — for a crossref repoint) ──────────────────

def id_catalogues(art: Dict) -> List[str]:
    """The id catalogues a dangling reference could resolve into — every realizable content id in
    the artifact, id + short name only, no prose. A crossref/compile fix repoints or creates
    against these EXACT ids."""
    from maestro.modules import assets, cast, combat, inventory, scenes, world
    out: List[str] = []
    out += cast.character_index(art)
    out += scenes.nodes_index_block(art)
    out += world.places_index_block(art)
    out += inventory.item_index(art)
    out += assets.location_index(art)
    out += combat.combat_index_block(art)
    return out


# ── per-error archetypes (what a Check points its `context` at) ──────────────

def ctx_structural(module, rd: Dict) -> str:
    """A structural repair on the module's OWN component (dedup, missing field, broken graph edge).
    Target + a compact self-view + run-state. No cross-module prose, no spec, no story state."""
    art = rd.get("artifact") or {}
    lines = target_block(rd) + module.self_digest(art) + tail_block(rd)
    lines += ["", "Make the one change that clears the target."]
    return "\n".join(lines)


def ctx_crossref(module, rd: Dict) -> str:
    """A dangling reference or a compile failure. The target names the bad ref/line; give the valid
    id catalogues it must resolve into (ids only) and the last read/compile output. No prose."""
    art = rd.get("artifact") or {}
    lines = target_block(rd) + id_catalogues(art) + tail_block(rd)
    lines += ["", "Repoint the reference to a real id above (or create the missing one)."]
    return "\n".join(lines)
