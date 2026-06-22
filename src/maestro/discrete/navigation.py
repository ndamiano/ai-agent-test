"""navigation — clickable rooms/screens. Owns `places`.

The spine when present: `places` stitches the whole script (the compiles-gated terminal), its
talk-hotspots `call` dialogue_npc nodes, and it carries the intrinsic build order (a place's
talk targets must exist first; everything sits on premise/assets). Adds the requirement that
backgrounds have ids (rooms need art) and contributes the place tools + the base action verbs.
"""

from maestro.modules import Module
from maestro.discrete.validators import v_places, SKEL_PLACES

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
             "noun": "place", "noun_plural": "PLACES"},
    action_verbs=("examine", "take", "talk", "move", "use", "win"),
    ir_slices={"places": "places", "start.place": "places"},
    projected=True,
)
