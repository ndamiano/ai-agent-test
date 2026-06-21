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

from maestro.modules import Module
from maestro.agent import make_node_subloop
from maestro.discrete.validators import v_nodes, SKEL_NODES

SPINE = Module(
    id="dialogue",
    components=("nodes",),
    schemas={"nodes": v_nodes},
    skeletons={"nodes": SKEL_NODES},
    baseline={
        "premise": [
            {"type": "count", "path": "premise.characters", "min": 3},
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
            {"type": "count", "path": "nodes.node_ids", "min": 20},
            {"type": "refs_resolve", "from": "premise.endings", "from_key": "id",
             "to": "nodes.node_ids"},
            {"type": "node_targets_resolve"},
            {"type": "reachable_from_start"},
            {"type": "min_branches", "min": 2},
            # One IR line == one dialogue beat; ~6 substantial beats ≈ the old 10-quoted-line target.
            {"type": "each_node_min_lines", "min": 6},
            {"type": "all_characters_speak"},
            {"type": "compiles"},
        ],
    },
    sub_runner=make_node_subloop,
    projected=True,
)

NPC = Module(
    id="dialogue_npc",
    components=("nodes",),
    schemas={"nodes": v_nodes},
    skeletons={"nodes": SKEL_NODES},
    baseline={"nodes": [
        {"type": "each_node_min_lines", "min": 3},
        # Catch a dangling node jump HERE (nodes mode, with node tools) rather than letting it
        # surface at the navigation spine's `compiles`, where only place tools are available.
        {"type": "node_targets_resolve"},
    ]},
    sub_runner=make_node_subloop,
    projected=True,
)
