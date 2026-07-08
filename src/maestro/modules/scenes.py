"""scenes — the conversation/scene graph. Authors the `nodes` component.

The playable script: a graph of nodes, each a list of dialogue lines plus an `end` that jumps,
branches (a menu), returns, or ends the game. A slot-guarded sub-loop grows the graph along
declared edges — a new node must fill an OPEN SLOT (a dangling target a written node already
points at), so the script grows in dramatic order instead of sprouting redundant siblings.

Example games:
  - "a band reunites at their drummer's funeral"      — cast + story + scenes
  - "explore a haunted manor and talk to its ghosts"  — cast + world + scenes
"""

from typing import Dict, List, Optional, Set

from maestro import context_render as cr
from maestro.ir_assemble import EMOTIONS as _EMOTIONS_TUPLE
from maestro.modules import checks, views
from maestro.modules.module import Check, Error, Module, load_prompt, register_module
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
                              "validate", "request_review"})
_T_WRITE = frozenset({"write_scene"})                                # add a scene (screenplay text)
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


_NARR_NAMES = {"narr", "narrator", "narration"}
_SPEAKER_RE = None  # compiled lazily; the format is `NAME: text` / `NAME [emotion]: text`


def parse_screenplay(script: str, characters: List[Dict]):
    """Screenplay text -> IR lines. `NAME: text`, optional `[emotion]` tag, NARR for narration;
    a line without a speaker prefix continues the previous line. Returns (lines, error)."""
    import re
    if not isinstance(script, str) or not script.strip():
        return None, "script must be non-empty screenplay text (`NAME: line` per line)"
    by_name = {}
    for c in characters or []:
        if c.get("id"):
            by_name[c["id"].lower()] = c["id"]
        if c.get("name"):
            by_name[c["name"].lower()] = c["id"]
    head = re.compile(r"^\s*([A-Za-z][\w .'-]*?)\s*(?:\[([a-z]+)\])?\s*:\s*(.*)$")
    lines: List[Dict] = []
    for raw in script.splitlines():
        if not raw.strip():
            continue
        m = head.match(raw)
        if not m:
            if lines:
                lines[-1]["text"] = (lines[-1]["text"] + " " + raw.strip()).strip()
                continue
            return None, (f"the script must start with a `NAME:` line — got {raw.strip()[:60]!r}. "
                          f"Every line is `NAME: text` (or `NARR:` for narration).")
        name, emotion, text = m.group(1).strip(), m.group(2), m.group(3).strip()
        key = name.lower()
        if key in _NARR_NAMES:
            speaker = None
        elif key in by_name:
            speaker = by_name[key]
        else:
            return None, (f"unknown speaker {name!r} — use one of "
                          f"{sorted(set(by_name.values()))} or NARR for narration.")
        if not text:
            continue
        line: Dict = {"speaker": speaker, "text": text}
        if emotion and speaker is not None:
            if emotion not in _EMOTIONS:
                return None, (f"emotion {emotion!r} on {name}'s line — use EXACTLY one of "
                              f"{sorted(_EMOTIONS)} (or no tag for neutral).")
            line["emotion"] = emotion
        lines.append(line)
    if not lines:
        return None, "script parsed to zero lines — write `NAME: text` per line"
    return lines, None


_EFFECT_KEYS = ("set_flag", "clear_flag", "add_item", "remove_item", "set_var", "add_var")


def effect_error(e) -> Optional[str]:
    """One narrative effect = ONE known key. Observed: invented effect types ('show_text')
    written to lines sailed through the dict-shape check and died attributed-but-unfixable at
    the terminal compile."""
    if not isinstance(e, dict) or not any(k in e for k in _EFFECT_KEYS):
        return (f"unknown effect {str(e)[:80]!r} — an effect is one of "
                f"{{\"set_flag\"|\"clear_flag\": \"<flag>\"}}, "
                f"{{\"add_item\"|\"remove_item\": \"<item>\"}}, "
                f"{{\"set_var\"|\"add_var\": {{\"var\": .., \"value\"|\"amount\": ..}}}}. "
                f"Prose belongs in the line's `text`, never in an effect.")
    return None


def end_error(end) -> Optional[str]:
    """Menu/end policy for BOTH write paths — write_node and edit_node's end patch. A fake
    menu written via the end-patch path games min_branches, so the gate must hold there too."""
    if not isinstance(end, dict) or end.get("type") not in _END_TYPES:
        return f"node.end must be an object whose 'type' is one of {sorted(_END_TYPES)}"
    if end.get("type") == "menu":
        choices = end.get("choices") or []
        for j, c in enumerate(choices):
            if not isinstance(c, dict) or not c.get("target"):
                return (f"menu choice [{j}] has no 'target' — every choice is "
                        f"{{text, target}} where target is the node it jumps to.")
            for e in c.get("effects") or []:
                err = effect_error(e)
                if err:
                    return f"menu choice [{j}].effects: {err}"
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
        effs = ln.get("effects")
        if effs is not None and (not isinstance(effs, list)
                                 or any(not isinstance(e, dict) for e in effs)):
            return (f"node.lines[{j}].effects must be a list of effect OBJECTS "
                    f"(e.g. {{\"set_flag\": \"found_key\"}}), not strings")
        for e in effs or []:
            err = effect_error(e)
            if err:
                return f"node.lines[{j}].effects: {err}"
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


_NODE_GUARD = {"count_tool": "write_scene", "id_key": "node_id", "id_list_key": "node_ids",
               "noun": "node", "assign": pick_slot, "prepare": _stamp_beat, "cap": _parallel_cap}


# ── the node-graph policy checks ──────────────────────────────────────────────
def reachable_from_start(artifact: Dict):
    node_ids, nodes = views.nodes_of(artifact)
    if not node_ids:
        # An empty graph has no orphans — failing here sends the fixer at nodes that don't
        # exist (observed: edit_node('start') hallucinated for 140 steps). Whether nodes
        # SHOULD exist is owned by build_nodes/beats_realized/nodes_world_entered.
        return True, None
    edges = {nid: views.node_targets(nodes.get(nid, {})) for nid in node_ids}
    # In a world game a node is also ENTERED from a place: every talk hotspot's target is a
    # root, not an orphan (observed: legitimately talk-entered nodes flagged unreachable and
    # the fixer told to wire them into the node graph they don't belong in).
    roots = [node_ids[0]]
    for place in ((artifact.get("places") or {}).get("places") or {}).values():
        for h in (place.get("interactables") or []) if isinstance(place, dict) else []:
            a = (h or {}).get("action") or {}
            if a.get("type") == "talk" and a.get("node"):
                roots.append(a["node"])
    orphans = [n for n in node_ids if n not in views.reachable(node_ids, edges, roots)]
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


def _force_ending_end(module, context, error, slot, services, dispatch) -> None:
    """A planned ending node's end.type is decided by the check itself — setting it is
    deterministic, so no LLM. Observed: the model DISAGREED with the check (it had authored a
    continuation node) and re-asserted the same jump 250 steps straight, every edit 'ok'."""
    services.allowed = None
    result = dispatch("edit_node", {"node_id": error.path, "end": {"type": "end"}})
    services._report(f"forced end on planned ending '{error.path}': "
                     + ("ok" if result.get("ok") else f"error — {result.get('error')}"))


def _d_ending_nodes_end(chk, m, ctx):
    """A planned ending node that doesn't end the game loops the player back into the story
    (observed from the turn-loop closer: an ending jumping to beat_02). The inverse of
    premature_endings: that check frees unplanned end nodes; this one pins planned ones."""
    if not _has_story(ctx.artifact):
        return []
    planned = {e.get("id") for e in (ctx.artifact.get("story") or {}).get("endings", [])}
    node_ids, nodes = views.nodes_of(ctx.artifact)
    return [Error(type=chk.tier, code=chk.code, component="nodes", path=nid,
                  message=f"'{nid}' is one of the story's planned endings but its end.type is "
                          f"{(nodes[nid].get('end') or {}).get('type')!r} — an ending node must "
                          f"END the game. Set its end to {{\"type\": \"end\"}} (edit_node).")
            for nid in node_ids
            if nid in planned and (nodes.get(nid, {}).get("end") or {}).get("type") != "end"]


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
    # In a walkable world the player's branching is spatial (which zone, which NPC) plus the
    # ending fork — conversations return to the map rather than menu-branch, so a dialogue-menu
    # floor doesn't apply and would stall (the world closer authors `return`, not menus).
    if not _has_story(ctx.artifact) or "world" in (ctx.spec.get("modules") or []):
        return []
    return m.wrap(chk, min_branches(ctx.artifact, min=ctx.param("min_branches", 1)))


def _d_all_characters_speak(chk, m, ctx):
    if not _has_story(ctx.artifact):
        return []
    return m.wrap(chk, all_characters_speak(ctx.artifact))


def _d_crossref(chk, m, ctx):
    if not _owns_compile(ctx.artifact):
        return []
    # A dangling item ref is DEMAND to author that item — inventory owns it (author, not repoint).
    # Without inventory composed, scenes still handles it (strip the reference).
    has_inv = "inventory" in (ctx.spec.get("modules") or [])
    return [Error(type=chk.tier, code=chk.code, component="nodes", message=rec["message"],
                  path=rec.get("path"), ref=rec.get("ref"), kind=rec.get("kind"))
            for rec in checks.crossref_failures(ctx.artifact)
            if not (has_inv and rec.get("kind") == "item")]


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
            out.append("  ALREADY ON SCREEN — the previous scene ended with these lines. They are "
                       "context ONLY: never re-emit or paraphrase them. Your scene starts with the "
                       "NEXT thing said or done, picking up their referents (objects, claims, "
                       "tensions) without contradicting them:")
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


# ── turn-loop scene authoring (one call per character turn) ───────────────────
_MAX_TURNS = 12
_TURN_MAX_TOKENS = 600
_ECHO_WORDS = 6


def _card_text(card: Dict) -> str:
    keep = ("name", "role", "voice", "temperament", "drive", "history", "competencies",
            "example_lines")
    out = []
    for k in keep:
        v = card.get(k)
        if not v:
            continue
        out.append(f"{k}: " + ("; ".join(str(x) for x in v) if isinstance(v, list) else str(v)))
    return "\n".join(out)


def _scene_brief(view: Dict, assigned: Optional[Dict], cast: List[Dict], artifact: Dict,
                 schema_facts: Optional[List[str]] = None) -> str:
    beats = {b["id"]: b for b in (view.get("beats") or []) if b.get("id")}
    beat_ids = list(beats)
    lines = ["THE SCENE:"]
    others = ", ".join(c.get("name", c["id"]) for c in cast)
    lines.append(f"Present: {others}.")
    bgs = (artifact.get("asset_manifest") or {}).get("backgrounds", [])
    if bgs:
        lines.append(f"Setting: {bgs[0].get('description', bgs[0].get('id'))}")
    # Each agent's system prompt carries only its OWN card; without the other side's fixed
    # facts, an agent under pressure borrows the other's biography (observed: Juniper claiming
    # Elara's flight abroad as her own). The spec's seeded facts anchor numbers/timelines too
    # (observed: "three years" / "since 2004" / "twelve years" drifting across scenes) — the
    # LIVE story-state facts are not used here: ending deltas pollute them with one branch's
    # outcome ("they drift apart") that would contradict the others.
    facts = list(schema_facts or [])
    for c in cast:
        bits = [c.get("drive", "")] + list(c.get("history") or [])[:1]
        bits = [b for b in bits if b]
        if bits:
            facts.append(f"{c.get('name', c['id'])}: " + " ".join(bits))
    if facts:
        lines.append("FIXED FACTS (never contradict these, never swap them between characters):")
        lines += [f"  {f}" for f in facts]
    bid = (assigned or {}).get("beat") or (beat_ids[0] if beat_ids else None)
    if bid and bid in beats:
        lines.append(f"This scene dramatizes: {render_beat(beats[bid])}")
        i = beat_ids.index(bid)
        if i + 1 < len(beat_ids):
            lines.append(f"It leads toward: {render_beat(beats[beat_ids[i + 1]])}")
    path = (assigned or {}).get("path") or []
    if path:
        crumb = " → ".join(p["synopsis"] or p["id"] for p in path)
        lines.append(f"Already happened: {crumb}")
    lead = (assigned or {}).get("lead_in") or []
    if lead:
        lines.append("The previous scene ended with (already on screen — do not repeat):")
        for ln in lead:
            lines.append(f"  {ln.get('speaker') or 'NARR'}: {ln.get('text', '')}")
    if not view.get("node_ids"):
        # Orientation drifts into speech without the second sentence (observed: "we're back in
        # this stripped-down room, trying to prove nothing has changed" SAID to the friend who
        # already knows).
        lines.append("This is the game's OPENING — the player knows nothing yet. Start your "
                     "first reply with one NARR line that orients them: who you two are to "
                     "each other, where this is, and why tonight. The orientation lives ONLY "
                     "in that NARR line — your spoken lines never explain your shared history "
                     "or why tonight matters; you both already know.")
    return "\n".join(lines)


_FIRST_SECOND = {"i", "you", "me", "my", "mine", "your", "yours", "we", "us", "our", "ours"}


def _speaks_12(text: str) -> bool:
    import re
    return any(w.split("'")[0].split("’")[0] in _FIRST_SECOND
               for w in re.findall(r"[A-Za-z'’]+", text.lower()))


def _other_narration(t: str, names: Set[str]) -> bool:
    """A 'spoken' line that is really third-person narration (observed: Marcus's turn emitting
    `Leo's breath hitches…` AND `Marcus reaches out and taps…` as his own speech; an RP-tuned
    model also writes pronoun prose: `He lowers the controller.`). Signature: opens on a cast
    name or He/She flowing straight into a lowercase sentence (or a possessive), with no I/you
    anywhere; a vocative ("Marcus you can't…") always carries a first/second person."""
    import re
    m = re.match(r"^([A-Za-z]+)(?:'s|’s)? [a-z]", t)
    if not m:
        return False
    head = m.group(1).lower()
    return (head in names or head in ("he", "she")) and not _speaks_12(t)


_ATTRIB_RE = None


def _unquote_attributed(t: str) -> str:
    """RP-tuned models emit novel-style dialogue: `'We got it,' he says, voice cracking.` —
    keep the quoted speech, drop the attribution tail."""
    import re
    m = re.match(r"^[\"'“‘](.+?)[,.!?]?[\"'”’]\s*,?\s*(?:he|she|\w+)\s+(?:says?|said|whisper|"
                 r"mutter|shout|repl|ask|call|scream|croak|manage)\w*\b.*$", t, re.I)
    if m:
        text = m.group(1).strip()
        return text if len(text.split()) >= 2 else t
    return t


def _parse_turn(reply: str, me: Dict, others: Optional[Set[str]] = None) -> List[Dict]:
    """A turn reply -> IR lines. Plain text = my speech; a `NARR:` line = my action. Strips an
    [END] marker (the caller checks for it), my own name prefix, and wrapping quotes. A line
    prefixed with ANOTHER character's name is the agent speaking for its scene partner
    (observed: Juniper's turn emitting `elara: ...`) — dropped, never re-attributed. A bare
    self-name glued to the front without a colon (observed: `Leo You're joking…` every turn)
    is stripped; narration-about-the-partner is re-attributed to the narrator."""
    import re
    out: List[Dict] = []
    my_names = {me["id"].lower(), (me.get("name") or "").lower()}
    other_names = {n.lower() for n in (others or set())}
    for raw in (reply or "").splitlines():
        t = raw.strip()
        if not t or t == "[END]":
            continue
        t = t.removesuffix("[END]").strip()
        # mechanics notes leak into play text (observed: "(flag: loophole_found)") — never speech
        t = re.sub(r"\(\s*(?:flag|set_flag|sets? flag)[^)]*\)", "", t, flags=re.I).strip()
        low = t.lower()
        if low.startswith("narr:") or low.startswith("narration:"):
            out.append({"speaker": None, "text": t.split(":", 1)[1].strip()})
            continue
        head = t.split(":", 1)
        if len(head) == 2:
            prefix = head[0].strip().lower()
            if prefix in other_names:
                continue
            if prefix in my_names:
                t = head[1].strip()
            elif "_" in prefix and re.fullmatch(r"[a-z][a-z0-9_]*", prefix):
                # a snake_case tag matching no cast name is a garbled speaker prefix
                # (observed: `arist_thorne:` for a cast id `aris_thorne`) — drop, never
                # re-attribute a line meant for someone else
                continue
        first, _, rest = t.partition(" ")
        if rest and first.lower() in my_names and rest[:1].isupper():
            t = rest.strip()
        t = _unquote_attributed(t)
        if len(t) >= 2 and t[0] in "\"'“" and t[-1] in "\"'”":
            t = t[1:-1].strip()
        if t:
            speaker = None if _other_narration(t, other_names | my_names) else me["id"]
            # A voice card that licenses rambling beats every prompt bound (observed: 129-word
            # single lines). Splitting at sentence ends preserves the text and keeps each line
            # sayable in one breath — and inside the dialogue box.
            for chunk in _split_breaths(t):
                out.append({"speaker": speaker, "text": chunk})
    return out


def _split_breaths(text: str, max_words: int = 55) -> List[str]:
    import re
    if len(text.split()) <= max_words:
        return [text]
    parts = re.split(r"(?<=[.!?…])\s+", text)
    chunks, cur = [], ""
    for p in parts:
        joined = (cur + " " + p).strip()
        if cur and len(joined.split()) > max_words:
            chunks.append(cur)
            cur = p
        else:
            cur = joined
    if cur:
        chunks.append(cur)
    return chunks


_FINISH_SCHEMA = [{"type": "function", "function": {
    "name": "finish_scene",
    "description": "File the finished scene: its exit, synopsis, and story-state delta.",
    "parameters": {"type": "object", "properties": {
        "end": {"type": "object", "description":
                "{type:'jump', target} | {type:'menu', choices:[{text,target}]} | "
                "{type:'return'} (hand back to a walkable map) | {type:'end'}"},
        "event_summary": {"type": "string"},
        "location": {"type": "string"},
        "story_state_delta": {"type": "object"},
    }, "required": ["end", "event_summary"]}}}]


def _world_end(end, ending_ids: set) -> Dict:
    """The exit for a conversation embedded in a walkable world. It is self-contained: it returns
    control to the map. The one exception is the story's climax — a menu whose every choice targets
    a story ending node — which is kept so the branching endings remain reachable from the game's
    final decision. Everything else (jump/end/malformed) collapses to `return`."""
    if isinstance(end, dict) and end.get("type") == "menu":
        kept = [c for c in (end.get("choices") or [])
                if isinstance(c, dict) and c.get("target") in ending_ids]
        targets = {c.get("target") for c in kept}
        if len(targets) >= 2:
            return {"type": "menu", "choices": kept}
    return {"type": "return"}


def scene_turn_loop(module, context, error, slot, services, dispatch) -> None:
    """Author one scene as a live conversation: each character is its own LLM call (system =
    its card; the scene so far = chat turns), a closer call files the exit + delta, and the
    result dispatches through the normal guarded write_scene path."""
    from renpy.templating import render_template
    from maestro.modules.module import _PROMPTS_DIR
    from maestro.services import parse_action

    art = context.artifact
    # A conversation embedded in a walkable world is self-contained — it returns control to the map
    # instead of chaining down the story spine (a talk that jumps beat→beat→ending plays the whole
    # game and ends it the instant you speak to anyone). Endings are reached out in the world.
    in_world = "world" in (context.spec.get("modules") or [])
    view = module.view(art) or {}
    assigned = pick_slot(view, slot)
    cast = [c for c in (art.get("characters") or {}).get("characters", []) if c.get("id")]
    if len(cast) < 2:
        services.run(module.get_correction_prompt(context, error, slot=slot), dispatch=dispatch)
        return
    node_id = (assigned or {}).get("id") or ("scene_01" if not view.get("node_ids")
                                             else f"scene_{(view.get('beats_todo') or ['x'])[0]}")
    sss = context.spec.get("story_state_schema") or {}
    schema_facts = list(sss.get("established_facts") or [])
    # entity states carry the RELATIONSHIP frame — without it strangers talk like old friends
    # (observed: "neutral_strangers" in the spec while the detainee first-names the officer).
    schema_facts += [f"{k}: {v}" for k, v in (sss.get("entity_states") or {}).items()
                     if isinstance(v, str)]
    brief = _scene_brief(view, assigned, cast, art, schema_facts=schema_facts)
    beats = {b["id"]: b for b in (view.get("beats") or []) if b.get("id")}
    bid = (assigned or {}).get("beat") or (next(iter(beats), None))
    business = render_beat(beats[bid]) if bid in beats else "the conversation reaches a turn"
    if bid in beats and str(beats[bid].get("tension", "")).strip().lower() in ("", "none"):
        business += ("\nStake: NONE — nothing needs to go wrong in this scene. Do the activity, "
                     "get the jokes in, let it be easy; it exists to build these people.")
    endings = {e.get("id"): e for e in (art.get("story") or {}).get("endings", [])}
    is_ending = node_id in endings
    if is_ending:
        # The raw ending id ("ending_integration") gets parroted back as dialogue when it's in
        # the instruction — only the concrete description enters the prompt.
        business = ("this scene is the story's ENDING — resolve it: "
                    f"{endings[node_id].get('description', '')}")
    min_lines = context.param("each_node_min_lines", 3)

    # The lead-in's last speaker just spoke — the OTHER character opens.
    lead = (assigned or {}).get("lead_in") or []
    last_speaker = next((ln.get("speaker") for ln in reversed(lead) if ln.get("speaker")), None)
    order = list(cast)
    if last_speaker and order[0]["id"] == last_speaker:
        order = order[1:] + order[:1]

    def _dupe_keys(text: str) -> List[str]:
        # Exact key + (for long lines) a first-words key: agents circle with the same opener and
        # a varied tail ("You're treating this lease like…/deadline like…" five turns straight),
        # so a repeated opening phrase counts as a repeat.
        t = " ".join(text.strip().lower().split())
        return [t] if len(t.split()) <= _ECHO_WORDS else [t, " ".join(t.split()[:4])]

    def _spent(text: str) -> bool:
        # A short line may recur ONCE — the deadpan echo ("We have time." / "We have time.")
        # is the register, not circling. Anything longer, or a third occurrence, is circling.
        cap = 2 if len(text.split()) <= _ECHO_WORDS else 1
        return any(seen.get(k, 0) >= cap for k in _dupe_keys(text))

    def _mark(text: str) -> None:
        for k in _dupe_keys(text):
            seen[k] = seen.get(k, 0) + 1

    other_names = {n for c in cast for n in (c["id"], c.get("name", "")) if n}
    transcript: List[Dict] = []
    seen: Dict[str, int] = {}
    for ln in lead:   # the lead-in is already on screen — re-emitting it is an echo
        _mark(ln.get("text", ""))
    # Long openers repeat ACROSS scenes too (observed: "The spawn rate on…" 4× in one script) —
    # seed every existing scene's long lines so a new scene can't reuse their openers. Short
    # lines stay free: a cross-scene deadpan callback is the register, not circling.
    for node in ((art.get("nodes") or {}).get("nodes") or {}).values():
        for ln in (node.get("lines") or []):
            t = (ln or {}).get("text", "")
            if isinstance(t, str) and len(t.split()) > _ECHO_WORDS:
                _mark(t)
    ended = False
    stale_turns = 0
    for turn in range(_MAX_TURNS):
        me = order[turn % len(order)]
        system = render_template(_PROMPTS_DIR / "scene_turn.txt",
                                 {"name": me.get("name", me["id"]),
                                  "card": _card_text(me), "business": business})
        msgs = [{"role": "system", "content": system}, {"role": "user", "content": brief}]
        for ln in transcript:
            if ln["speaker"] == me["id"]:
                msgs.append({"role": "assistant", "content": ln["text"]})
            else:
                who = "NARR" if ln["speaker"] is None else ln["speaker"]
                text = f"{who}: {ln['text']}"
                if msgs[-1]["role"] == "user":
                    msgs[-1]["content"] += "\n" + text
                else:
                    msgs.append({"role": "user", "content": text})
        if msgs[-1]["role"] == "assistant":
            msgs.append({"role": "user", "content": "(your turn continues the scene)"})
        # Agents never volunteer [END] (observed: 8/8 scenes chopped at the turn cap, last
        # line dangling) — the last two rounds tell them the scene is closing.
        # "land the business" made agents close by SAYING the theme ("It wasn't just tactics.
        # It was trust.") — the close instruction must forbid the summary shape outright.
        if turn >= _MAX_TURNS - 2 * len(order):
            msgs[-1]["content"] += ("\n(Bring the scene to a close now — with an action or a "
                                    "short line, never a summary of what the scene meant — "
                                    "and put [END] on its own line after your reply.)")
        # reasoning="none": a turn is speech, not a puzzle — thinking tokens eat the whole
        # budget and truncate before any text is emitted (observed: 400/400 tokens, content null).
        resp = services.infer(msgs, None, reasoning="none", max_tokens=_TURN_MAX_TOKENS,
                              dialogue=True)
        reply = ((resp.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
        mine = {c.get("name", "") for c in cast if c["id"] == me["id"]} | {me["id"]}
        lines = _parse_turn(reply, me, others=other_names - mine)
        # Two agents deadlock by re-trading the same exchange (observed: one line verbatim 4×).
        # A repeated line never enters the transcript; a turn that contributes NOTHING new is
        # stale, and two stale turns in a row mean the conversation is spent — close the scene.
        fresh = []
        for ln in lines[:3]:
            if _spent(ln["text"]):
                continue
            _mark(ln["text"])
            fresh.append(ln)
        if fresh:
            transcript.extend(fresh)
            stale_turns = 0
            services._report(f"turn {turn + 1} ({me['id']}): {fresh[0]['text'][:60]}")
        else:
            stale_turns += 1
        if len(transcript) >= min_lines and ("[END]" in reply or stale_turns >= 2):
            ended = "[END]" in reply
            break
    if len(transcript) < min_lines:
        services._report(f"turn loop produced only {len(transcript)} lines — abandoning scene")
        return

    script = "\n".join(f"{ln['speaker'] or 'NARR'}: {ln['text']}" for ln in transcript)
    close_system = load_prompt("scene_close_world.txt" if in_world else "scene_close.txt")
    node_lines = ["CURRENT NODES: " + ", ".join(view.get("node_ids") or ["(none)"])]
    beat_ids = list(beats)
    if bid in beats and beat_ids.index(bid) + 1 < len(beat_ids):
        node_lines.append(f"NEXT BEAT: {render_beat(beats[beat_ids[beat_ids.index(bid) + 1]])}")
    endings = (art.get("story") or {}).get("endings", [])
    if endings:
        # earned_by was write-only data before this — the closer decides menus, so it is the
        # one consumer that can make a choice actually pay toward its planned ending.
        earned = {p.get("ending"): p.get("earned_by")
                  for p in (art.get("story") or {}).get("ending_paths", []) if isinstance(p, dict)}
        node_lines.append("STORY ENDINGS (a menu choice toward one must MATCH what earns it):")
        for e in endings:
            eid = e.get("id", "")
            node_lines.append(f"  {eid}" + (f" — earned by: {earned[eid]}" if earned.get(eid) else ""))
    bgs = (art.get("asset_manifest") or {}).get("backgrounds", [])
    if bgs:
        node_lines.append("LOCATIONS: " + ", ".join(b.get("id", "") for b in bgs))
    close_user = "\n".join(node_lines) + "\n\nTHE SCRIPT:\n" + script
    action = {}
    for _ in range(2):
        resp = services.infer([{"role": "system", "content": close_system},
                               {"role": "user", "content": close_user}], _FINISH_SCHEMA,
                              reasoning="none", dialogue=True)
        action = parse_action(resp, _FINISH_SCHEMA)
        if action.get("tool") == "finish_scene" and isinstance(action.get("args"), dict) \
                and action["args"].get("end"):
            break
    args = action.get("args") or {}
    end = args.get("end")
    beat_ix = {b: i for i, b in enumerate(beat_ids)}
    my_ix = beat_ix.get(bid, -1)
    existing = (art.get("nodes") or {}).get("nodes") or {}

    def _backward(target: str) -> bool:
        # A jump/choice into an existing node at or behind this beat loops the story
        # (observed: beat_08's closer jumping to beat_02).
        tb = (existing.get(target) or {}).get("beat")
        return tb in beat_ix and beat_ix[tb] <= my_ix

    nxt = beat_ids[my_ix + 1] if 0 <= my_ix and my_ix + 1 < len(beat_ids) else None
    fallback = {"type": "jump", "target": f"scene_{nxt}"} if nxt else {"type": "end"}
    if is_ending:
        # A planned ending node ENDS the game — the closer has no discretion here (observed:
        # an ending jumping back into beat_02, another ending in a self-targeting menu).
        end = {"type": "end"}
    elif in_world:
        # Map conversation: hand back to the world. The ONLY non-return exit is a climactic
        # menu whose choices ARE the story endings, so branching endings stay reachable.
        # (`endings` was rebound to the raw ending LIST above, so derive its ids here.)
        end = _world_end(end, {e.get("id") for e in endings
                               if isinstance(e, dict) and e.get("id")})
    elif not isinstance(end, dict) or end.get("type") not in _END_TYPES:
        end = fallback
    elif end.get("type") == "jump" and _backward(end.get("target", "")):
        end = fallback
    elif end.get("type") == "menu":
        kept = [c for c in (end.get("choices") or [])
                if isinstance(c, dict) and not _backward(c.get("target", ""))]
        targets = {c.get("target") for c in kept if c.get("target")}
        if len(targets) < 2:
            # A fake menu (every choice → the same scene) would be rejected at write_scene,
            # throwing the whole authored scene away — collapse it to the jump it really is.
            end = {"type": "jump", "target": next(iter(targets))} if targets else fallback
        elif len(kept) >= 2:
            end = {"type": "menu", "choices": kept}
        else:
            end = fallback
    delta = args.get("story_state_delta") if isinstance(args.get("story_state_delta"), dict) else {}
    if args.get("event_summary"):
        delta["event_summary"] = args["event_summary"]
    result = dispatch("write_scene", {
        "node_id": node_id, "script": script, "end": end,
        "location": args.get("location") or (bgs[0]["id"] if bgs else None),
        "story_state_delta": delta})
    services._report(f"write_scene({node_id}): "
                     + ("ok" if result.get("ok") else f"error — {result.get('error')}")
                     + ("" if ended else " (turn cap reached)"))


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
        Check("beats_realized", _d_beats_realized, tools=_T_WRITE, guard=_NODE_GUARD,
              prompt="nodes_screenplay_write.txt", skeleton="", run=scene_turn_loop),
        Check("build_nodes", _d_build_nodes, tools=_T_WRITE, guard=_NODE_GUARD,
              prompt="nodes_screenplay_write.txt", skeleton="", run=scene_turn_loop),
        Check("endings_are_nodes", _d_endings_are_nodes, tools=_T_EDIT_WRITE),
        Check("ending_nodes_end", _d_ending_nodes_end, job="fix", prompt="nodes_fix.txt",
              tools=_T_EDIT, run=_force_ending_end),
        Check("premature_endings", _d_premature_endings, job="fix", prompt="nodes_fix.txt",
              tools=_T_EDIT, context=cr.ctx_structural),
        Check("node_targets_resolve", lambda chk, m, ctx: m.wrap(chk, node_targets_resolve(ctx.artifact)),
              job="fix", prompt="nodes_fix.txt", tools=_T_EDIT_WRITE, context=cr.ctx_crossref),
        Check("reachable_from_start", lambda chk, m, ctx: m.wrap(chk, reachable_from_start(ctx.artifact)),
              job="fix", prompt="nodes_fix.txt", tools=_T_EDIT, context=cr.ctx_structural),
        Check("each_node_min_lines", lambda chk, m, ctx: m.wrap(chk, each_node_min_lines(ctx.artifact, min=ctx.param("each_node_min_lines", 3))), tools=_T_EDIT_WRITE),
        Check("each_node_has_location", lambda chk, m, ctx: m.wrap(chk, each_node_has_location(ctx.artifact)),
              job="fix", prompt="nodes_fix.txt", tools=_T_EDIT, context=cr.ctx_crossref),
        Check("no_dead_gates", lambda chk, m, ctx: m.wrap(chk, no_dead_gates(ctx.artifact)),
              job="fix", prompt="nodes_fix.txt", tools=_T_EDIT, context=cr.ctx_structural),
        Check("min_branches", _d_min_branches, tools=_T_EDIT_WRITE),
        Check("all_characters_speak", _d_all_characters_speak, tools=_T_EDIT_WRITE),
        Check("crossref", _d_crossref, job="fix", when_clean=True,
              build_prompt=cr.crossref_correction, tools=_T_EDIT_WRITE),
        Check("compiles", _d_compiles, job="fix", when_clean=True, prompt="nodes_fix.txt",
              tools=_T_EDIT_WRITE, context=cr.ctx_crossref),
    ]

    def params(self) -> Dict:
        return {"min_branches": 1, "each_node_min_lines": 3}

    def render_context(self, ctx: Dict) -> str:
        # The dialogue AUTHOR's context, crafted: WHO speaks (full character cards — the quality
        # path), WHERE it happens (locations with descriptions, for staging), WHAT the story drives
        # at (question/endings), which item ids exist to move, plus the graph view + assigned slot
        # with its real lead-in lines, and the LIVE continuity tail. Scene TEXT never enters except
        # the lead-in; the cumulative established_facts dump never does.
        from maestro.modules import assets, cast, inventory, story
        art = ctx.get("artifact") or {}
        lines = cr.premise_block(ctx) + [""] + cr.target_block(ctx)
        lines += cast.character_cards(art)
        lines += assets.locations_block(art)
        lines += story.story_block(art)
        lines += inventory.item_index(art)
        view = ctx.get("active_view") or {}
        if view.get("node_ids"):
            lines += _node_view_block(view, ctx.get("slot_index", 0))
        else:
            lines += _render_slot_focus(view, ctx.get("slot_index", 0))
        lines += cr.story_tail_block(ctx)
        lines += cr.tail_block(ctx)
        lines += ["", "Call one tool to address the first to-do item."]
        return "\n".join(lines)

    def self_digest(self, artifact: Dict) -> list:
        return nodes_index_block(artifact)


MODULE = Scenes()
register_module(MODULE)
