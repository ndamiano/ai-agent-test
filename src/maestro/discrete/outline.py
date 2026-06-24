"""outline — the dramatic structure of a visual novel. Owns `outline`.

The 20-node middle used to be improvised: each node was authored seeing only the story-state
snapshot, so the script had no global arc — no pacing, escalation, or setup/payoff across scenes.
This module inserts a beat-sheet stage between premise and nodes: a small, frozen arc (logline +
ordered beats + a planned path to each ending) the node sub-loop then realizes. Because a passing
upstream component is injected into every later step's context (see executor.build_context), the
outline reaches `write_node` for free — the nodes author against the arc, not blind.

VN-only: it ships with the `vn` preset (the story-spine genre). A point-and-click/card game uses
`dialogue_npc` for supporting barks and has no dramatic spine to outline.
"""

from maestro.modules import Module
from maestro import context_render as cr
from maestro.discrete.validators import v_outline, SKEL_OUTLINE


def _outline_view(c):
    """Upstream view for the node author: drop the full beat list. The node sub-loop gets the
    relevant beat WINDOW (previous/this/next) from its slot focus, so dumping every beat into its
    locked-upstream context is dead weight. logline + ending_paths stay (the arc's anchors)."""
    return {k: v for k, v in c.items() if k != "beats"}


def _render_context(ctx):
    return "\n".join(
        cr.spec_block(ctx) + [""] + cr.todo_block(ctx.get("todo", []))
        + cr.scratchpad_block(ctx) + cr.upstream_block(ctx.get("upstream") or {})
        + cr.tail_block(ctx) + ["", "Call one tool to address the first to-do item."])

# The VN build order: premise -> (asset_manifest, outline) -> nodes. Declared here (the new
# participant) the way navigation declares its chain — so enforce_baseline sets nodes' dep on the
# outline authoritatively, not leaving it to the proposer LLM.
_DEPS = {
    "premise": [],
    "asset_manifest": ["premise"],
    "outline": ["premise"],
    "nodes": ["premise", "asset_manifest", "outline"],
}

MODULE = Module(
    id="outline",
    components=("outline",),
    schemas={"outline": v_outline},
    skeletons={"outline": SKEL_OUTLINE},
    deps=_DEPS,
    baseline={"outline": [
        {"type": "exists", "path": "outline.logline"},
        {"type": "count", "path": "outline.beats", "min": 5},
        {"type": "each_has", "path": "outline.beats",
         "fields": ["id", "summary", "purpose", "tension"]},
        {"type": "distinct", "path": "outline.beats", "key": "id"},
        {"type": "each_has", "path": "outline.ending_paths", "fields": ["ending", "earned_by"]},
        # Every premise ending must have a planned path — forces the arc to aim at all of them,
        # so the branches diverge by design instead of cosmetically. Attributed to outline (the
        # declaring component), which is what the agent fixes by adding the missing path.
        {"type": "refs_resolve", "from": "premise.endings", "from_key": "id",
         "to": "outline.ending_paths", "to_key": "ending"},
    ]},
    mode_tools=frozenset({"write_component", "update_scratchpad", "request_review"}),
    mode_prompt="mode_outline.txt",
    context_view=_outline_view,
    render_context=_render_context,
)
