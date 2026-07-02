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
from maestro.modules.story import render_beat

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


# ── write-time node policy (what write_node/edit_node enforce; tools.py just dispatches) ──────
_MAX_MENU_CHOICES = 3


def normalize_narration(content):
    """'narrator'/empty speakers become null narration in place."""
    for ln in content.get("lines", []) or []:
        if isinstance(ln, dict) and isinstance(ln.get("speaker"), str) \
                and ln["speaker"].strip().lower() in ("", "narrator", "narration", "none", "null"):
            ln["speaker"] = None
    return content


def end_error(end) -> Optional[str]:
    """Menu/end policy for BOTH write paths — write_node and edit_node's end patch. A fake
    menu written via the end-patch path games min_branches, so the gate must hold there too."""
    if not isinstance(end, dict) or end.get("type") not in _END_TYPES:
        return f"node.end must be an object whose 'type' is one of {sorted(_END_TYPES)}"
    if end.get("type") == "menu":
        choices = end.get("choices") or []
        n = len(choices)
        if n > _MAX_MENU_CHOICES:
            return (f"this menu has {n} choices — a menu is a DRAMATIC FORK, at most "
                    f"{_MAX_MENU_CHOICES} divergent paths, not a room/location picker. Cut it to "
                    f"the {_MAX_MENU_CHOICES} choices that actually matter; for linear flow use "
                    f"end.type 'jump' and let the NEXT scene branch. Build depth, not width.")
        if choices and all(isinstance(c, dict) and c.get("requires") for c in choices):
            return ("every choice in this menu is gated by `requires` — if none match at runtime the "
                    "menu is empty and the game dead-ends. Leave at least ONE choice ungated as a "
                    "guaranteed fallback path.")
        targets = {c.get("target") for c in choices if isinstance(c, dict) and c.get("target")}
        if len(choices) >= 2 and len(targets) < 2:
            return ("every choice in this menu leads to the SAME scene — that's a fake choice, not a "
                    "fork. Either make the choices lead to DIFFERENT targets (a real branch), or drop "
                    "the menu and use end.type 'jump' for a single continuation.")
    return None


def node_write_error(content, *, min_lines: int = 0):
    """Reject a malformed/thin IR node up front so a wrong shape steers immediately instead of
    failing schema/crossref/compile later. Patchable fields (location) are NOT rejected here:
    a reject forces the model to regenerate the whole scene and retries degrade (scrambled
    speakers, prose-mode drift) — the each_node_has_location check repairs them with a
    single-field edit instead."""
    if not isinstance(content, dict):
        return "node content must be a JSON object {lines, end}"
    lines = content.get("lines")
    if not isinstance(lines, list) or not lines:
        return "node.lines must be a non-empty list of {speaker, text} objects"
    for j, ln in enumerate(lines):
        if not isinstance(ln, dict) or not ln.get("text"):
            return f"node.lines[{j}] needs a non-empty 'text' (speaker is optional; null = narration)"
        emo = ln.get("emotion")
        if emo and emo not in _EMOTIONS:
            return (f"node.lines[{j}] has emotion {emo!r} — use EXACTLY one of "
                    f"{sorted(_EMOTIONS)} (or omit for neutral)")
    err = end_error(content.get("end"))
    if err:
        return err
    if len(lines) < min_lines:
        return (f"a node needs at least {min_lines} lines/beats — this has {len(lines)}. Write the "
                f"FULL scene now (several dialogue beats with subtext), not a stub; thin nodes are "
                f"rejected.")
    return None


# ── the compact graph projection + slot math (the system picks where the next scene goes) ─────
def node_view(artifact: Dict) -> Dict:
    node_ids, nodes = views.nodes_of(artifact)
    synopses = (artifact.get("nodes", {}) or {}).get("synopses", {}) or {}
    edges = {nid: views.node_targets(nodes.get(nid, {})) for nid in node_ids}
    reach = views.reachable(node_ids, edges)
    written = set(node_ids)
    entry = node_ids[0] if node_ids else None

    # An OPEN SLOT is a target a written node already points at but that does not exist yet — the
    # only place a new node may legitimately go. Each carries the path that leads to it (ancestor
    # synopses) and the parent's actual closing lines (the text the new scene continues).
    slots: Dict[str, Dict] = {}
    for src, tgt, label in views.references(node_ids, nodes):
        if tgt in written:
            continue
        slots.setdefault(tgt, {"id": tgt, "from": []})["from"].append({"node": src, "label": label})
    beats_full = [b for b in (artifact.get("story", {}) or {}).get("beats", []) if b.get("id")]
    beat_ids = [b["id"] for b in beats_full]
    beat_index = {bid: i for i, bid in enumerate(beat_ids)}
    covered = {nodes.get(nid, {}).get("beat") for nid in node_ids}
    uncovered = [b for b in beat_ids if b not in covered]

    for tgt, slot in slots.items():
        parent = slot["from"][0]["node"]
        path_ids = views.shortest_path(entry, parent, edges) if entry else []
        slot["path"] = [{"id": pid, "synopsis": synopses.get(pid, "")} for pid in path_ids]
        slot["lead_in"] = [
            {"speaker": ln.get("speaker"), "text": ln.get("text", "")}
            for ln in (nodes.get(parent, {}).get("lines") or [])[-6:] if isinstance(ln, dict)]
        pbeat = nodes.get(parent, {}).get("beat")
        if pbeat in beat_index:
            nxt = beat_index[pbeat] + 1
            slot["beat"] = beat_ids[nxt] if nxt < len(beat_ids) else None
        else:
            slot["beat"] = uncovered[0] if uncovered else None

    return {
        "node_ids": node_ids,
        "edges": {nid: sorted(set(e)) for nid, e in edges.items()},
        "reachable": sorted(reach),
        "unreachable": [n for n in node_ids if n not in reach],
        "line_counts": {nid: len(nodes.get(nid, {}).get("lines", [])) for nid in node_ids},
        "synopses": synopses,
        "open_slots": sorted(slots.values(), key=lambda s: s["id"]),
        "beats": beats_full,
        "beats_todo": uncovered,
    }


def nodes_index_block(artifact: Dict) -> List[str]:
    """Which scenes EXIST (id + one-line synopsis) — how nodes present themselves in OTHER
    modules' prompts (talk targets, on_victory), never the scene text itself."""
    ns = artifact.get("nodes") or {}
    ids = ns.get("node_ids") or []
    if not ids:
        return []
    syn = ns.get("synopses") or {}
    return ["", "SCENES (existing dialogue node ids — a talk/on_victory target is one of these):",
            *(f"  {nid}" + (f' — "{syn[nid]}"' if syn.get(nid) else "") for nid in ids)]


def pick_slot(view: Dict, index: int = 0) -> Optional[Dict]:
    """The system — not the author — chooses which scene to write next: the open slot whose beat
    comes earliest in the story, so the spine is built in dramatic order. `index` selects the
    index-th slot in that order — parallel fixes each get their own (worker i writes slot i)."""
    slots = view.get("open_slots") or []
    if index >= len(slots):
        return None
    order = {b["id"]: i for i, b in enumerate(view.get("beats") or []) if b.get("id")}
    last = len(order)
    return sorted(slots, key=lambda s: (order.get(s.get("beat"), last), s["id"]))[index]


def beat_for_new_node(view: Dict, chosen: Optional[Dict], has_existing: bool) -> Optional[str]:
    """The beat the system stamps on the node being written — it picked the slot, so it owns the
    beat too. The assigned slot's beat; the first beat for the opening node; the first
    still-unrealized beat for an escape-hatch branch root."""
    if chosen is not None:
        return chosen.get("beat")
    beat_ids = [b["id"] for b in (view.get("beats") or []) if b.get("id")]
    if not has_existing:
        return beat_ids[0] if beat_ids else None
    todo = view.get("beats_todo") or []
    return todo[0] if todo else None


def _stamp_beat(view: Dict, assigned: Optional[Dict], args: Dict) -> Dict:
    beat = beat_for_new_node(view, assigned, bool(view.get("node_ids")))
    return {**args, "beat": beat} if beat else args


def _parallel_cap(view: Dict) -> int:
    """One worker on an empty graph (parallel roots = a forest); otherwise one per open slot."""
    if not view.get("node_ids"):
        return 1
    return max(1, len(view.get("open_slots") or ()))


_NODE_GUARD = {"count_tool": "write_node", "id_key": "node_id", "id_list_key": "node_ids",
               "noun": "node", "assign": pick_slot, "prepare": _stamp_beat, "cap": _parallel_cap}


# ── the node-graph policy checks ──────────────────────────────────────────────
def reachable_from_start(artifact: Dict):
    node_ids, nodes = views.nodes_of(artifact)
    if not node_ids:
        return False, "no nodes to reach"
    edges = {nid: views.node_targets(nodes.get(nid, {})) for nid in node_ids}
    orphans = [n for n in node_ids if n not in views.reachable(node_ids, edges)]
    if orphans:
        return False, f"nodes unreachable from '{node_ids[0]}': {orphans[:5]}"
    return True, None


def node_targets_resolve(artifact: Dict):
    node_ids, nodes = views.nodes_of(artifact)
    ids = set(node_ids)
    bad = [f"{nid} -> {tgt}" for nid in node_ids
           for tgt in views.node_targets(nodes.get(nid, {})) if tgt not in ids]
    if bad:
        return False, (f"node jump/menu targets that don't exist: {bad[:5]} — either create those "
                       f"nodes (write_node) or repoint the jump to an existing node (edit_node).")
    return True, None


def min_branches(artifact: Dict, *, min=1):
    _, nodes = views.nodes_of(artifact)
    n = sum(1 for node in nodes.values() if (node.get("end", {}) or {}).get("type") == "menu")
    if n < min:
        return False, f"only {n} menu(s), need {min} — add player choices (end.type 'menu')"
    return True, None


def each_node_min_lines(artifact: Dict, *, min=3):
    node_ids, nodes = views.nodes_of(artifact)
    thin = [f"{nid} ({len(nodes.get(nid, {}).get('lines', []))})"
            for nid in node_ids if len(nodes.get(nid, {}).get("lines", [])) < min]
    if thin:
        return False, f"nodes with < {min} lines: {thin[:5]} — give them more beats"
    return True, None


def each_node_has_location(artifact: Dict):
    node_ids, nodes = views.nodes_of(artifact)
    known = [b.get("id") for b in (artifact.get("asset_manifest") or {}).get("backgrounds", [])
             if b.get("id")]
    missing = [nid for nid in node_ids if not nodes.get(nid, {}).get("location")]
    if missing:
        return False, (f"nodes with no location/background: {missing[:5]} — set each node's "
                       f"`location` to a background id from {known} (edit_node location='bg_...').")
    if known:
        bad = [nid for nid in node_ids if nodes.get(nid, {}).get("location") not in known]
        if bad:
            return False, (f"nodes whose location is not in asset_manifest: {bad[:5]} — set each "
                           f"to one of {known} (edit_node location='bg_...').")
    return True, None


def unrealized_beats(artifact: Dict) -> List[str]:
    """Story beats with no scene dramatizing them yet (empty when there's no story). Each one is a
    slot the scene author still owes a node for."""
    beats = [b.get("id") for b in (artifact.get("story", {}) or {}).get("beats", []) if b.get("id")]
    if not beats:
        return []
    _, nodes = views.nodes_of(artifact)
    covered = {n.get("beat") for n in nodes.values() if n.get("beat")}
    return [b for b in beats if b not in covered]


def no_dead_gates(artifact: Dict):
    _, nodes = views.nodes_of(artifact)
    set_in: Dict[str, set] = {}
    for nid, node in nodes.items():
        for eff in checks.node_effects(node):
            for t in checks.effect_targets(eff):
                set_in.setdefault(t, set()).add(nid)
    dead = []
    for nid, node in nodes.items():
        end = node.get("end", {}) or {}
        if end.get("type") != "menu":
            continue
        for ch in end.get("choices", []) or []:
            for ref in checks.cond_state_refs(ch.get("requires")):
                if not (set_in.get(ref, set()) - {nid}):
                    dead.append(f"{nid} (gates on '{ref}')")
    if dead:
        return False, (f"choices gated on state that is never raised in an earlier scene: {dead[:5]} "
                       f"— the gate can't open, so the branch is dead. Either raise it with an effect "
                       f"in an EARLIER node (add_var/set_flag), or DROP the `requires` so the choice "
                       f"is always available (endings can be earned by the story, not a variable).")
    return True, None


def all_characters_speak(artifact: Dict):
    # `isinstance str` guards: a mis-typed speaker/id (the model nesting an object) must not crash the
    # set build with `unhashable type: 'dict'` — it's excluded and caught by the schema/other checks.
    chars = {c.get("id") for c in artifact.get("characters", {}).get("characters", [])
             if isinstance(c, dict) and isinstance(c.get("id"), str)}
    if not chars:
        return False, "characters component has no characters"
    _, nodes = views.nodes_of(artifact)
    spoke = {ln.get("speaker") for node in nodes.values()
             for ln in node.get("lines", []) if isinstance(ln, dict) and isinstance(ln.get("speaker"), str)}
    silent = sorted(chars - spoke)
    if silent:
        return False, f"characters who never speak: {silent} — give them lines"
    return True, None


def _d_beats_realized(chk, m, ctx):
    art = ctx.artifact
    if not _has_story(art):
        return []
    missing = unrealized_beats(art)
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


def _d_premature_endings(chk, m, ctx):
    """An UNPLANNED ending is fine — a good exit can evolve naturally from play. What's not fine
    is ending the game while the arc is barely started: an unplanned end node whose path never
    reaches the story's final beats cuts the player off from the whole story."""
    if not _has_story(ctx.artifact):
        return []
    story = ctx.artifact.get("story") or {}
    planned = {e.get("id") for e in story.get("endings", [])}
    beat_index = {b["id"]: i for i, b in enumerate(story.get("beats", [])) if b.get("id")}
    if len(beat_index) < 3:
        return []
    node_ids, nodes = views.nodes_of(ctx.artifact)
    entry = node_ids[0] if node_ids else None
    edges = {nid: views.node_targets(nodes.get(nid, {})) for nid in node_ids}
    out = []
    for nid in node_ids:
        if (nodes.get(nid, {}).get("end") or {}).get("type") != "end" or nid in planned:
            continue
        path = views.shortest_path(entry, nid, edges) if entry else []
        if not path:
            continue  # unreachable — reachable_from_start owns that failure
        reached = max((beat_index.get(nodes.get(p, {}).get("beat"), -1) for p in path),
                      default=-1)
        if reached < len(beat_index) - 2:
            out.append(Error(
                type=chk.tier, code=chk.code, component="nodes", path=nid,
                message=f"scene '{nid}' ends the game while the story has barely started (its "
                        f"path only reaches beat {reached + 1} of {len(beat_index)}). An "
                        f"unplanned ending is welcome only once the arc has played out — change "
                        f"this `end` to a jump that continues the story instead."))
    return out


def _d_min_branches(chk, m, ctx):
    if not _has_story(ctx.artifact):
        return []
    return m.wrap(chk, min_branches(ctx.artifact, min=ctx.param("min_branches", 1)))


def _d_all_characters_speak(chk, m, ctx):
    if not _has_story(ctx.artifact):
        return []
    return m.wrap(chk, all_characters_speak(ctx.artifact))


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
    slot = pick_slot(view, slot_index)

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
        lead = slot.get("lead_in") or []
        if lead and len(slot.get("from", [])) == 1:
            out.append("  THE SCENE CONTINUES FROM these exact lines (pick up their referents — "
                       "objects, claims, tensions — do not re-invent or contradict them):")
            for ln in lead:
                out.append(f"    {ln.get('speaker') or 'narration'}: {ln.get('text', '')}")
        elif len(slot.get("from", [])) > 1:
            out.append("  CONVERGENCE POINT — several different scenes lead here. Do not continue "
                       "any one of them mid-thought: open on something that reads correctly from "
                       "ANY entry (a new moment in this location), then move the beat forward.")
        bid = slot.get("beat")
        if bid and bid in by_id:
            i = beat_ids.index(bid)
            if i > 0:
                out.append(f"  PREVIOUS BEAT (behind us): {render_beat(by_id[beat_ids[i - 1]])}")
            out.append(f"  THIS NODE'S BEAT (dramatize it): {render_beat(by_id[bid])}")
            if i + 1 < len(beat_ids):
                out.append(f"  NEXT BEAT (aim here — your `end` opens a slot toward it): "
                           f"{render_beat(by_id[beat_ids[i + 1]])}")
        else:
            if beat_ids:
                out.append(f"  PREVIOUS BEAT (behind us): {render_beat(by_id[beat_ids[-1]])}")
            out.append("  THIS NODE IS AN ENDING — realize a story ending (end.type 'end'); the "
                       "arc resolves here, so open no further slot.")
        out.append("Your `end` continues the spine: prefer a single `jump` toward the next beat; "
                   "use a `menu` ONLY at a real fork, never to list places to visit.")
        return out

    if not view.get("node_ids"):
        out += ["", "WRITE THE OPENING NODE — no scenes exist yet. Choose its node_id.",
                "  There is NO prior text to continue: this scene ESTABLISHES the game's reality, "
                "and every later scene builds on what it claims. The player arrives knowing "
                "NOTHING — open with one or two narration lines (speaker null) that orient them: "
                "who these people are to each other, where they are, and why tonight (the "
                "premise). THEN the dialogue starts mid-task. Anchor it in the CHARACTER CARDS "
                "and the location's description. Introduce at most one or two concrete objects "
                "and make them matter — each claim here is one the rest of the story must live "
                "with."]
        if beat_ids:
            out.append(f"  FIRST BEAT (dramatize it): {render_beat(by_id[beat_ids[0]])}")
            if len(beat_ids) > 1:
                out.append(f"  NEXT BEAT (aim here): {render_beat(by_id[beat_ids[1]])}")
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
    projector = staticmethod(node_view)
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
        Check("premature_endings", _d_premature_endings, job="fix", prompt="nodes_fix.txt",
              tools=_T_EDIT),
        Check("node_targets_resolve", lambda chk, m, ctx: m.wrap(chk, node_targets_resolve(ctx.artifact)), job="fix", prompt="nodes_fix.txt", tools=_T_EDIT_WRITE),
        Check("reachable_from_start", lambda chk, m, ctx: m.wrap(chk, reachable_from_start(ctx.artifact)), job="fix", prompt="nodes_fix.txt", tools=_T_EDIT),
        Check("each_node_min_lines", lambda chk, m, ctx: m.wrap(chk, each_node_min_lines(ctx.artifact, min=ctx.param("each_node_min_lines", 3))), tools=_T_EDIT_WRITE),
        Check("each_node_has_location", lambda chk, m, ctx: m.wrap(chk, each_node_has_location(ctx.artifact)), job="fix", prompt="nodes_fix.txt", tools=_T_EDIT),
        Check("no_dead_gates", lambda chk, m, ctx: m.wrap(chk, no_dead_gates(ctx.artifact)),
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
        # The dialogue author's context, crafted: WHO speaks (full character cards), WHERE it
        # happens (locations with descriptions), WHAT the story is driving at (question/endings),
        # which items exist to move, plus the graph view + assigned slot with its real lead-in
        # lines. Scene TEXT never enters except the lead-in — the window is the budget.
        from maestro.modules import assets, cast, inventory, story
        art = ctx.get("artifact") or {}
        lines = cr.spec_block(ctx) + [""] + cr.todo_block(ctx.get("todo", []))
        lines += cr.target_block(ctx)
        lines += cast.character_cards(art)
        lines += assets.locations_block(art)
        lines += story.story_block(art)
        lines += inventory.items_block(art)
        view = ctx.get("active_view") or {}
        if view.get("node_ids"):
            lines += _node_view_block(view, ctx.get("slot_index", 0))
        else:
            lines += _render_slot_focus(view, ctx.get("slot_index", 0))
        lines += cr.story_state_block(ctx)
        lines += cr.tail_block(ctx)
        lines += ["", "Call one tool to address the first to-do item."]
        return "\n".join(lines)


MODULE = Scenes()
register_module(MODULE)
