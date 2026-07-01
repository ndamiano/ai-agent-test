"""scenes — the conversation/scene graph. Authors the `nodes` component.

The playable script: a graph of nodes, each a list of dialogue lines plus an `end` that jumps,
branches (a menu), returns, or ends the game. A slot-guarded sub-loop grows the graph along
declared edges — a new node must fill an OPEN SLOT (a dangling target a written node already
points at), so the script grows in dramatic order instead of sprouting redundant siblings.

Example games:
  - "a band reunites at their drummer's funeral"      — cast + story + scenes
  - "explore a haunted manor and talk to its ghosts"  — cast + world + scenes
"""

from typing import Dict, List, Optional

from maestro import context_render as cr
from maestro.ir_assemble import EMOTIONS as _EMOTIONS_TUPLE
from maestro.modules import checks, views
from maestro.modules.module import Check, Error, Module, register_module

_END_TYPES = {"jump", "menu", "return", "end"}
_EMOTIONS = set(_EMOTIONS_TUPLE)


def v_nodes(c: Dict) -> Optional[str]:
    node_ids = c.get("node_ids")
    nodes = c.get("nodes")
    if not isinstance(node_ids, list) or not node_ids:
        return "nodes.node_ids must be a non-empty list of node id strings"
    if not isinstance(nodes, dict):
        return "nodes.nodes must be an object mapping node_id -> {lines, end}"
    if "start" in node_ids:
        return ("do not use 'start' as a node id — the compiler adds 'label start' that "
                "jumps to node_ids[0]")
    for nid in node_ids:
        node = nodes.get(nid)
        if not isinstance(node, dict):
            return f"nodes.nodes is missing an object for node id '{nid}'"
        lines = node.get("lines")
        if not isinstance(lines, list) or not lines:
            return f"nodes.nodes['{nid}'].lines must be a non-empty list of {{speaker, text}}"
        for ln in lines:
            emo = isinstance(ln, dict) and ln.get("emotion")
            if emo and emo not in _EMOTIONS:
                return (f"nodes.nodes['{nid}'] has line emotion {emo!r}; "
                        f"must be one of {sorted(_EMOTIONS)}")
        end = node.get("end")
        if not isinstance(end, dict) or end.get("type") not in _END_TYPES:
            return f"nodes.nodes['{nid}'].end must have a 'type' in {sorted(_END_TYPES)}"
    return None


SKEL_NODES = (
    '{\n'
    '  "node_ids": ["scene_01", "scene_02", "ending_<slug>"],  // do NOT include "start"\n'
    '  "nodes": {\n'
    '    "scene_01": {\n'
    '      "location": "bg_<place>",\n'
    '      "lines": [\n'
    '        {"speaker": "<char_a>", "text": "...", "emotion": "happy"},\n'
    '        {"speaker": null, "text": "narration has speaker null"},\n'
    '        {"speaker": "<char_b>", "text": "...", "emotion": "angry"}\n'
    '      ],\n'
    '      "end": {"type": "menu", "choices": [\n'
    '        {"text": "the choice that leads one way", "target": "ending_<slug>"},\n'
    '        {"text": "the choice that leads another", "target": "ending_<other>"}\n'
    '      ]}\n'
    '    }\n'
    '  }\n'
    '}\n'
    '// OPTIONAL state: you MAY add "flags"/"variables" and move them with `effects`; but a choice\n'
    '//   with `requires` opens only if that state was raised by an effect in an EARLIER scene —\n'
    '//   never gate on a value it can\'t reach, and always leave one ungated choice in a menu.\n'
    '// You write JSON, the compiler renders it (escaping/layout handled).\n'
    '// location = a background asset id from asset_manifest.backgrounds; it sets the scene\n'
    '//   image and every character who speaks in the node is shown over it. Tag EVERY node.\n'
    '// speaker = an EXACT characters id, or null for narration (no "narrator").\n'
    '// emotion (spoken lines only) = the speaker\'s expression on this line: one of\n'
    '//   neutral, happy, sad, angry, surprised, worried. Pick the one the line conveys so\n'
    '//   the character\'s face changes as they talk; omit for neutral. Ignored on narration.\n'
    '// end.type is one of: jump {target}, menu {choices:[{text,target,requires?,effects?}]},\n'
    '//   return (back to caller), end {ending?} (a definitive ending).\n'
    '// Every jump/menu target MUST be a node you also create, AND every node must be\n'
    '//   reachable: some node jumps/menus to it. Each story.endings id is its own node.\n'
    '// effects (on a line / choice): set_flag, clear_flag, add_item, remove_item,\n'
    '//   set_var{var,value}, add_var{var,delta}. Declare flags/variables here.'
)

_NODE_MODE_TOOLS = frozenset({"write_node", "edit_node", "read_node", "read_story_state",
                              "validate", "update_scratchpad", "request_review"})
_T_WRITE = frozenset({"write_node"})                                 # add a node
_T_EDIT = frozenset({"read_node", "edit_node"})                      # correct an existing node
_T_EDIT_WRITE = frozenset({"read_node", "edit_node", "write_node"})  # correct OR add
_NODE_GUARD = {"count_tool": "write_node", "id_key": "node_id", "id_list_key": "node_ids",
               "noun": "node"}


def _d_beats_realized(chk, m, ctx):
    art = ctx.artifact
    if not _has_story(art):
        return []
    missing = checks.unrealized_beats(art)
    return checks.slot_errors(len(missing), type=chk.tier, code=chk.code,
                              component="nodes", noun="scene") if missing else []


def _d_build_nodes(chk, m, ctx):
    # No story to realize and scenes owns the entry: still need one scene to play. (With `world`,
    # nodes are demand-driven by talk-hotspots, so don't bootstrap orphans.)
    art = ctx.artifact
    with_world = "world" in (ctx.spec.get("modules") or [])
    if _has_story(art) or not _owns_compile(art) or with_world:
        return []
    if checks.length(art, "nodes.node_ids") < 1:
        return checks.slot_errors(1, type=chk.tier, code=chk.code, component="nodes", noun="scene")
    return []


def _d_endings_are_nodes(chk, m, ctx):
    if not _has_story(ctx.artifact):
        return []
    return m.wrap(chk, checks.refs_resolve(ctx.artifact, "story.endings", "nodes.node_ids",
                                           from_key="id"))


def _d_min_branches(chk, m, ctx):
    if not _has_story(ctx.artifact):
        return []
    return m.wrap(chk, checks.min_branches(ctx.artifact, min=ctx.param("min_branches", 1)))


def _d_all_characters_speak(chk, m, ctx):
    if not _has_story(ctx.artifact):
        return []
    return m.wrap(chk, checks.all_characters_speak(ctx.artifact))


def _d_crossref(chk, m, ctx):
    if not _owns_compile(ctx.artifact):
        return []
    return [Error(type=chk.tier, code=chk.code, component="nodes", message=rec["message"],
                  path=rec.get("path"), ref=rec.get("ref"))
            for rec in checks.crossref_failures(ctx.artifact)]


def _d_compiles(chk, m, ctx):
    if not _owns_compile(ctx.artifact):
        return []
    return m.wrap(chk, checks.compile_failure(ctx.run_dir, ctx.engine))


def _render_slot_focus(view: Dict, slot_index: int = 0) -> List[str]:
    out: List[str] = []
    beats = view.get("beats") or []
    by_id = {b["id"]: b for b in beats if b.get("id")}
    beat_ids = [b["id"] for b in beats if b.get("id")]
    slot = cr.pick_slot(view, slot_index)

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
            out.append("  THIS NODE IS AN ENDING — realize a story ending (end.type 'end'); the "
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
                "Every existing scene's path is fully written, but these story beats still have no "
                "scene: " + ", ".join(todo) + ". Give an existing node a jump/menu to a NEW node id, "
                "then write that node to dramatize one."]
    return out


def _node_view_block(view: Dict, slot_index: int = 0) -> List[str]:
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
    ] + _render_slot_focus(view, slot_index)


def _has_story(art: Dict) -> bool:
    return bool((art.get("story") or {}).get("central_question"))


def _owns_compile(art: Dict) -> bool:
    """scenes holds the compile/crossref terminal when there are nodes and no places (a pure scene
    graph). When places exist, `world` owns the IR entry and the whole-IR backstop."""
    has_nodes = bool((art.get("nodes") or {}).get("node_ids"))
    has_places = bool((art.get("places") or {}).get("place_ids"))
    return not has_places and (has_nodes or "nodes" not in art)


class Scenes(Module):
    id = "scenes"
    description = ("A branching, choice-driven dialogue/scene graph — the playable script. With "
                   "`story` it IS the game (a visual novel); with `world` it supplies room "
                   "conversations.")
    requires = ("cast",)
    priority = 50
    component = "nodes"
    mode_prompt = "nodes_write.txt"
    mode_tools = _NODE_MODE_TOOLS
    skeleton = SKEL_NODES
    skeletons = {"nodes": SKEL_NODES}
    schemas = {"nodes": v_nodes}
    projector = staticmethod(views.node_view)
    projected = True
    emits_compile = True   # a realization terminal: `nodes` stays writable to the end + needs locations

    # Collect the whole batch of node errors (nothing blocks); the compile terminal (crossref then
    # the real build) is `when_clean` — it appends only once the cheaper checks pass, so a stubborn
    # lint can't starve node creation. Adding a node (realizing a beat / bootstrapping the first
    # scene) is slot-guarded; every other error is a single edit.
    checks = [
        Check("beats_realized", _d_beats_realized, tools=_T_WRITE, guard=_NODE_GUARD),
        Check("build_nodes", _d_build_nodes, tools=_T_WRITE, guard=_NODE_GUARD),
        Check("endings_are_nodes", _d_endings_are_nodes, tools=_T_EDIT_WRITE),
        Check("node_targets_resolve", lambda chk, m, ctx: m.wrap(chk, checks.node_targets_resolve(
            ctx.artifact)), job="fix", prompt="nodes_fix.txt", tools=_T_EDIT_WRITE),
        Check("reachable_from_start", lambda chk, m, ctx: m.wrap(chk, checks.reachable_from_start(
            ctx.artifact)), job="fix", prompt="nodes_fix.txt", tools=_T_EDIT),
        Check("each_node_min_lines", lambda chk, m, ctx: m.wrap(chk, checks.each_node_min_lines(
            ctx.artifact, min=ctx.param("each_node_min_lines", 3))), tools=_T_EDIT_WRITE),
        Check("each_node_has_location", lambda chk, m, ctx: m.wrap(chk, checks.each_node_has_location(
            ctx.artifact)), job="fix", prompt="nodes_fix.txt", tools=_T_EDIT),
        Check("no_dead_gates", lambda chk, m, ctx: m.wrap(chk, checks.no_dead_gates(ctx.artifact)),
              job="fix", prompt="nodes_fix.txt", tools=_T_EDIT),
        Check("min_branches", _d_min_branches, tools=_T_EDIT_WRITE),
        Check("all_characters_speak", _d_all_characters_speak, tools=_T_EDIT_WRITE),
        Check("crossref", _d_crossref, job="fix", when_clean=True, prompt="nodes_fix.txt",
              tools=_T_EDIT_WRITE),
        Check("compiles", _d_compiles, job="fix", when_clean=True, prompt="nodes_fix.txt",
              tools=_T_EDIT_WRITE),
    ]

    def params(self) -> Dict:
        return {"min_branches": 1, "each_node_min_lines": 3}

    def render_context(self, ctx: Dict) -> str:
        lines = cr.spec_block(ctx) + [""] + cr.todo_block(ctx.get("todo", []))
        lines += cr.target_block(ctx)
        lines += cr.scratchpad_block(ctx)
        lines += cr.upstream_block(ctx.get("upstream") or {})
        view = ctx.get("active_view") or {}
        if view.get("node_ids"):
            lines += _node_view_block(view, ctx.get("slot_index", 0))
        lines += cr.story_state_block(ctx)
        lines += cr.tail_block(ctx)
        lines += ["", "Call one tool to address the first to-do item."]
        return "\n".join(lines)


MODULE = Scenes()
register_module(MODULE)
