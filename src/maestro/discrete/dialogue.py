"""dialogue — the conversation graph. Owns `nodes`.

Two configurations of the same component, because dialogue plays two structurally different
roles (decomposition discipline: a thing that behaves differently IS a different module):

  SPINE  — dialogue IS the game (visual novel). Rich cast + multiple endings + branching, and
           `nodes` is the compiles-gated terminal that stitches the whole script. Layers its
           extra requirements onto premise/asset_manifest.
  NPC    — dialogue is supporting barks a navigation game's talk-hotspots call. Light floor;
           navigation is the spine/terminal, so premise stays minimal and nodes just needs body.

Both own `nodes` (same schema/skeleton/sub-loop); they never appear in the same composition.
"""

from typing import Dict, List

from maestro.modules import Module
from maestro import context_render as cr
from maestro.discrete.validators import v_nodes, SKEL_NODES


def _render_slot_focus(view: Dict) -> List[str]:
    """Hand the author ONE scene to write: its node_id, the path that leads to it, and the beat
    window (previous beat behind us, this node's beat to dramatize, next beat to aim at) so it
    continues the arc. The list of every open slot is gone — the system picks the slot, the author
    just writes it."""
    out: List[str] = []
    beats = view.get("beats") or []
    by_id = {b["id"]: b for b in beats if b.get("id")}
    beat_ids = [b["id"] for b in beats if b.get("id")]
    slot = cr.pick_slot(view)

    if slot:
        srcs = ", ".join(f'{r["node"]} → "{r["label"]}"' for r in slot.get("from", []))
        out += ["",
                f"WRITE THIS NODE NEXT — node_id = {slot['id']} (use this EXACT id). It is the scene "
                f"reached from: {srcs}."]
        path = slot.get("path") or []
        if path:
            crumb = " → ".join(f'{p["id"]} "{p["synopsis"]}"' if p.get("synopsis") else p["id"]
                               for p in path)
            out.append(f"  PATH TO HERE (already happened — do NOT repeat it): {crumb}")
        bid = slot.get("beat")
        if bid and bid in by_id:
            i = beat_ids.index(bid)
            if i > 0:
                out.append(f"  PREVIOUS BEAT (behind us): {cr.render_beat(by_id[beat_ids[i - 1]])}")
            out.append(f"  THIS NODE'S BEAT (dramatize it): {cr.render_beat(by_id[bid])}")
            if i + 1 < len(beat_ids):
                out.append(f"  NEXT BEAT (aim here — your `end` opens a slot toward it): "
                           f"{cr.render_beat(by_id[beat_ids[i + 1]])}")
        else:
            if beat_ids:
                out.append(f"  PREVIOUS BEAT (behind us): {cr.render_beat(by_id[beat_ids[-1]])}")
            out.append("  THIS NODE IS AN ENDING — realize a premise ending (end.type 'end'); the "
                       "arc resolves here, so open no further slot.")
        out.append("Your `end` continues the spine: prefer a single `jump` toward the next beat; "
                   "use a `menu` ONLY at a real fork, never to list places to visit.")
        return out

    if not view.get("node_ids"):
        out += ["", "WRITE THE OPENING NODE — no scenes exist yet. Choose its node_id."]
        if beat_ids:
            out.append(f"  FIRST BEAT (dramatize it): {cr.render_beat(by_id[beat_ids[0]])}")
            if len(beat_ids) > 1:
                out.append(f"  NEXT BEAT (aim here): {cr.render_beat(by_id[beat_ids[1]])}")
        return out

    todo = view.get("beats_todo")
    if todo:
        out += ["",
                "Every existing scene's path is fully written, but these outline beats still have no "
                "scene: " + ", ".join(todo) + ". Give an existing node a jump/menu to a NEW node id, "
                "then write that node to dramatize one."]
    return out


def _node_view_block(view: Dict) -> List[str]:
    edges = view.get("edges", {})
    counts = view.get("line_counts", {})
    synopses = view.get("synopses", {})
    unreachable = set(view.get("unreachable", []))
    node_lines = [
        f"  {nid} -> {edges.get(nid, [])}"
        f"  ({'UNREACHABLE' if nid in unreachable else 'reachable'}, {counts.get(nid, 0)} lines)"
        + (f'  — "{synopses[nid]}"' if synopses.get(nid) else "")
        for nid in view["node_ids"]
    ]
    return [
        "",
        "CURRENT NODES (these already exist — reuse these EXACT ids; jump ONLY to an id "
        "listed here or to a node you also create this step):",
        *node_lines,
    ] + _render_slot_focus(view)


def _render_context(ctx: Dict) -> str:
    lines = cr.spec_block(ctx) + [""] + cr.todo_block(ctx.get("todo", []))
    lines += cr.target_block(ctx)
    lines += cr.scratchpad_block(ctx)
    lines += cr.upstream_block(ctx.get("upstream") or {})
    view = ctx.get("active_view") or {}
    if view.get("node_ids"):
        lines += _node_view_block(view)
    lines += cr.story_state_block(ctx)
    lines += cr.tail_block(ctx)
    lines += ["", "Call one tool to address the first to-do item."]
    return "\n".join(lines)


def _render_progress(view: Dict) -> str:
    """The note the sub-loop appends after each step: the live node list (with synopses) + the
    next slot to write, so the author tracks ids and keeps building the spine without rebuilding
    the whole context."""
    ids = view.get("node_ids")
    if not ids:
        return ""
    syn = view.get("synopses") or {}
    listing = ", ".join(f'{i} "{syn[i]}"' if syn.get(i) else i for i in ids)
    note = [f"CURRENT NODES: {listing}"]
    if view.get("unreachable"):
        note.append(f"UNREACHABLE: {view['unreachable']}")
    note += _render_slot_focus(view)
    return "\n".join(note)


# Node sub-loop gating — shared by both configurations (both own `nodes`, same loop). The decider
# and sub-loop expose these tools while in nodes mode; per-target gating narrows further.
_NODE_MODE_TOOLS = frozenset({"write_node", "edit_node", "read_node", "read_story_state",
                              "validate", "update_scratchpad", "request_review"})
_NODE_PROMPTS = {"author": "write_node.txt", "fix": "fix_node.txt"}
# Building/growing content = author; making existing nodes wire up or compile = fix. Unlisted → author.
_NODE_TARGET_JOBS = {
    "beats_realized": "author", "each_node_min_lines": "author",
    "min_branches": "author", "all_characters_speak": "author",
    "reachable_from_start": "fix", "node_targets_resolve": "fix",
    "each_node_has_location": "fix", "no_dead_gates": "fix",
    "crossref": "fix", "compiles": "fix",
}
# Per-target tool gating: each structural goal needs only a few tools. While driving the creation
# target (`beats_realized`), read/edit let the model fixate on an existing node instead of writing
# new ones; reachability is fixed by repointing an existing node's end (edit only), so write_node is
# withheld there.
_NODE_TARGET_TOOLS = {
    "beats_realized": frozenset({"write_node"}),
    "each_node_min_lines": frozenset({"read_node", "write_node", "edit_node"}),
    "no_dead_gates": frozenset({"read_node", "edit_node"}),
    "reachable_from_start": frozenset({"read_node", "edit_node"}),
    "each_node_has_location": frozenset({"read_node", "edit_node"}),
    "node_targets_resolve": frozenset({"read_node", "edit_node", "write_node"}),
    "min_branches": frozenset({"read_node", "edit_node", "write_node"}),
    "all_characters_speak": frozenset({"read_node", "edit_node", "write_node"}),
    "crossref": frozenset({"read_node", "edit_node", "write_node"}),
    "compiles": frozenset({"read_node", "edit_node", "write_node"}),
}
# create_targets: the targets that ADD nodes (vs. edit existing) — they get the slot guard (a new
# node must fill an open slot / continue the spine). beats_realized is the node creator now.
_NODE_SUBLOOP = {"count_tool": "write_node", "id_key": "node_id", "id_list_key": "node_ids",
                 "noun": "node", "create_targets": frozenset({"beats_realized"})}
_NODE_GATING = dict(
    mode_tools=_NODE_MODE_TOOLS, mode_prompt="write_node.txt", prompts=_NODE_PROMPTS,
    target_jobs=_NODE_TARGET_JOBS, target_tools=_NODE_TARGET_TOOLS, subloop=_NODE_SUBLOOP,
    render_context=_render_context, render_progress=_render_progress,
    # A dangling node reference (jump/start.node) routes to nodes mode, which has the node tools —
    # not to the navigation spine's compiles gate, where only place tools exist.
    ir_slices={"nodes": "nodes", "start.node": "nodes"},
)

SPINE = Module(
    id="dialogue",
    components=("nodes",),
    schemas={"nodes": v_nodes},
    skeletons={"nodes": SKEL_NODES},
    **_NODE_GATING,
    baseline={
        "premise": [
            # min 2, not 3: a 3-cast floor forced two-handers (a sisters drama, a duologue) to
            # invent a filler third "character" — often a narrator/environment entity that then has
            # to speak (fighting speaker:null narration) and has no sprite. 2 fits an intimate cast;
            # a bigger story raises it naturally.
            {"type": "count", "path": "premise.characters", "min": 2},
            {"type": "each_has", "path": "premise.characters",
             "fields": ["voice", "temperament", "drive", "history",
                        "competencies", "example_lines"]},
            {"type": "count", "path": "premise.endings", "min": 3},
            {"type": "distinct", "path": "premise.endings", "key": "id"},
        ],
        "asset_manifest": [
            {"type": "each_has", "path": "asset_manifest.characters", "fields": ["id"]},
        ],
        "nodes": [
            # Beat coverage, not a node-count quota: a fixed count (was 20) rewarded padding —
            # filler rooms that funnel back. Anchoring scenes to outline beats makes the arc the
            # spine and bounds length to what the story needs.
            {"type": "beats_realized"},
            {"type": "refs_resolve", "from": "premise.endings", "from_key": "id",
             "to": "nodes.node_ids"},
            {"type": "node_targets_resolve"},
            {"type": "reachable_from_start"},
            # One genuine fork is enough for a short VN (the climax); a write-time guard forbids
            # fake forks (all choices → one target), so we don't push the model to manufacture a
            # second menu it then fills with an illusory choice.
            {"type": "min_branches", "min": 1},
            # State is optional (endings are earned by the beats, not required to be mechanical),
            # but a gate that can never open ships a dead branch — forbid those.
            {"type": "no_dead_gates"},
            # One IR line == one dialogue beat; ~6 substantial beats ≈ the old 10-quoted-line target.
            {"type": "each_node_min_lines", "min": 6},
            {"type": "each_node_has_location"},
            {"type": "all_characters_speak"},
            {"type": "crossref"},
            {"type": "compiles"},
        ],
    },
    projected=True,
)

NPC = Module(
    id="dialogue_npc",
    components=("nodes",),
    schemas={"nodes": v_nodes},
    skeletons={"nodes": SKEL_NODES},
    **_NODE_GATING,
    baseline={"nodes": [
        {"type": "each_node_min_lines", "min": 3},
        # Catch a dangling node jump HERE (nodes mode, with node tools) rather than letting it
        # surface at the navigation spine's `compiles`, where only place tools are available.
        {"type": "node_targets_resolve"},
    ]},
    projected=True,
)
