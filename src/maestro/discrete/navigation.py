"""navigation — clickable rooms/screens. Owns `places`.

The spine when present: `places` stitches the whole script (the compiles-gated terminal), its
talk-hotspots `call` dialogue_npc nodes, and it carries the intrinsic build order (a place's
talk targets must exist first; everything sits on premise/assets). Adds the requirement that
backgrounds have ids (rooms need art) and contributes the place tools + the base action verbs.
"""

from typing import Dict, List

from maestro.modules import Module
from maestro import context_render as cr
from maestro.discrete.validators import v_places, SKEL_PLACES


def _place_view_block(view: Dict) -> List[str]:
    edges = view.get("edges", {})
    counts = view.get("interactable_counts", {})
    unreachable = set(view.get("unreachable", []))
    place_lines = [
        f"  {pid} -> {edges.get(pid, [])}"
        f"  ({'UNREACHABLE' if pid in unreachable else 'reachable'}, "
        f"{counts.get(pid, 0)} interactables)"
        for pid in view["place_ids"]
    ]
    extra = []
    if view.get("items_never_taken"):
        extra.append(f"items never taken: {view['items_never_taken']}")
    if view.get("items_never_used"):
        extra.append(f"items never used: {view['items_never_used']}")
    return [
        "",
        "CURRENT PLACES (these already exist — reuse these EXACT ids; `move` ONLY to a place "
        "id listed here or one you also create this step):",
        *place_lines,
        *(["  " + " | ".join(extra)] if extra else []),
    ]


def _render_context(ctx: Dict) -> str:
    lines = cr.spec_block(ctx) + [""] + cr.todo_block(ctx.get("todo", []))
    lines += cr.target_block(ctx)
    lines += cr.scratchpad_block(ctx)
    lines += cr.upstream_block(ctx.get("upstream") or {})
    view = ctx.get("active_view") or {}
    if view.get("place_ids"):
        lines += _place_view_block(view)
    lines += cr.story_state_block(ctx)
    lines += cr.tail_block(ctx)
    lines += ["", "Call one tool to address the first to-do item."]
    return "\n".join(lines)


def _render_progress(view: Dict) -> str:
    ids = view.get("place_ids")
    if not ids:
        return ""
    note = [f"CURRENT PLACES: {', '.join(ids)}"]
    if view.get("unreachable"):
        note.append(f"UNREACHABLE: {view['unreachable']}")
    return "\n".join(note)


# Talk-hotspots call dialogue nodes that must exist first; places sit on premise/assets/nodes.
_DEPS = {
    "premise": [],
    "asset_manifest": ["premise"],
    "nodes": ["premise"],
    "places": ["premise", "asset_manifest", "nodes"],
}

MODULE = Module(
    id="navigation",
    components=("places",),
    schemas={"places": v_places},
    skeletons={"places": SKEL_PLACES},
    baseline={
        "asset_manifest": [
            {"type": "each_has", "path": "asset_manifest.backgrounds", "fields": ["id"]},
        ],
        # The win condition is NOT here — it's the `goal` module family (goal_flag / goal_endless),
        # composed per game. Navigation only knows about moving between reachable places.
        "places": [
            {"type": "exists", "path": "places.start_place"},
            {"type": "count", "path": "places.place_ids", "min": 3},
            {"type": "each_place_min_interactables", "min": 2},
            {"type": "places_reachable"},
            {"type": "items_obtainable"},
            {"type": "items_used"},
            {"type": "crossref"},
            {"type": "compiles"},
        ],
    },
    deps=_DEPS,
    tool_names=("write_place", "edit_place", "add_interactable", "read_place", "set_places_meta"),
    mode_tools=frozenset({"write_component", "write_place", "edit_place", "add_interactable",
                          "read_place", "set_places_meta", "read_component", "validate",
                          "update_scratchpad", "request_review"}),
    mode_prompt="write_place.txt",
    prompts={"author": "write_place.txt", "fix": "fix_place.txt"},
    # Building/growing content = author; wiring/compile fixes = fix. Unlisted → author.
    target_jobs={
        "count": "author", "each_place_min_interactables": "author",
        "items_obtainable": "author", "items_used": "author",
        "places_reachable": "fix", "goal_reachable": "fix",
        "crossref": "fix", "compiles": "fix",
    },
    # Per-target gating: count ADDS places (write_place); reachability/goal are fixed by repointing
    # actions (edit), so write_place is withheld where adding would not help.
    target_tools={
        "count": frozenset({"write_component", "write_place"}),
        "each_place_min_interactables": frozenset({"read_place", "add_interactable", "edit_place"}),
        "items_obtainable": frozenset({"read_place", "add_interactable", "edit_place", "set_places_meta"}),
        "items_used": frozenset({"read_place", "add_interactable", "edit_place", "set_places_meta"}),
        "places_reachable": frozenset({"read_place", "add_interactable", "edit_place", "read_component"}),
        "goal_reachable": frozenset({"read_place", "add_interactable", "edit_place", "set_places_meta", "read_component"}),
        # crossref/compiles fixes are often cross-component: repoint a dangling talk-node (edit_place),
        # declare a missing variable/flag (set_places_meta), add a missing hotspot (add_interactable).
        "crossref": frozenset({"read_place", "edit_place", "add_interactable", "set_places_meta",
                               "write_place", "read_component"}),
        "compiles": frozenset({"read_place", "edit_place", "add_interactable", "set_places_meta",
                               "write_place", "read_component"}),
    },
    subloop={"count_tool": "write_place", "id_key": "place_id", "id_list_key": "place_ids",
             "noun": "place"},
    render_context=_render_context,
    render_progress=_render_progress,
    action_verbs=("examine", "take", "talk", "move", "use", "win"),
    ir_slices={"places": "places", "start.place": "places"},
    projected=True,
)
