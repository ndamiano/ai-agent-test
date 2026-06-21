"""navigation — clickable rooms/screens. Owns `places`.

The spine when present: `places` stitches the whole script (the compiles-gated terminal), its
talk-hotspots `call` dialogue_npc nodes, and it carries the intrinsic build order (a place's
talk targets must exist first; everything sits on premise/assets). Adds the requirement that
backgrounds have ids (rooms need art) and contributes the place tools + the base action verbs.
"""

from maestro.modules import Module
from maestro.agent import make_place_subloop
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
            {"type": "compiles"},
        ],
    },
    deps=_DEPS,
    tool_names=("write_place", "edit_place", "add_interactable", "read_place", "set_places_meta"),
    sub_runner=make_place_subloop,
    action_verbs=("examine", "take", "talk", "move", "use", "win"),
    projected=True,
)
