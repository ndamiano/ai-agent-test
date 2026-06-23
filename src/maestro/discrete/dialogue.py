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
                 "noun": "node", "noun_plural": "NODES", "create_targets": frozenset({"beats_realized"})}
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
