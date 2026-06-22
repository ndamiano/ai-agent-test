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
from maestro.discrete.validators import v_nodes, SKEL_NODES

# Node sub-loop gating — shared by both configurations (both own `nodes`, same loop). The decider
# and sub-loop expose these tools while in nodes mode; per-target gating narrows further.
_NODE_MODE_TOOLS = frozenset({"write_node", "edit_node", "read_node", "read_story_state",
                              "validate", "update_scratchpad", "request_review"})
_NODE_PROMPTS = {"author": "write_node.txt", "fix": "fix_node.txt"}
# Building/growing content = author; making existing nodes wire up or compile = fix. Unlisted → author.
_NODE_TARGET_JOBS = {
    "count": "author", "each_node_min_lines": "author",
    "min_branches": "author", "all_characters_speak": "author",
    "reachable_from_start": "fix", "node_targets_resolve": "fix",
    "crossref": "fix", "compiles": "fix",
}
# Per-target tool gating: each structural goal needs only a few tools. While driving `count`,
# read/edit let the model fixate on an existing node instead of writing new ones; reachability is
# fixed by repointing an existing node's end (edit only), so write_node is withheld there.
_NODE_TARGET_TOOLS = {
    "count": frozenset({"write_node"}),
    "each_node_min_lines": frozenset({"read_node", "write_node", "edit_node"}),
    "reachable_from_start": frozenset({"read_node", "edit_node"}),
    "node_targets_resolve": frozenset({"read_node", "edit_node", "write_node"}),
    "min_branches": frozenset({"read_node", "edit_node", "write_node"}),
    "all_characters_speak": frozenset({"read_node", "edit_node", "write_node"}),
    "crossref": frozenset({"read_node", "edit_node", "write_node"}),
    "compiles": frozenset({"read_node", "edit_node", "write_node"}),
}
_NODE_SUBLOOP = {"count_tool": "write_node", "id_key": "node_id", "id_list_key": "node_ids",
                 "noun": "node", "noun_plural": "NODES"}
_NODE_GATING = dict(
    mode_tools=_NODE_MODE_TOOLS, mode_prompt="write_node.txt", prompts=_NODE_PROMPTS,
    target_jobs=_NODE_TARGET_JOBS, target_tools=_NODE_TARGET_TOOLS, subloop=_NODE_SUBLOOP,
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
