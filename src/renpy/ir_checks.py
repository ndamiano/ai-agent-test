"""Structured story/game checks over the Game IR components.

The generic checks (exists/count/...) can't express "is this a real game" — reachable scenes,
branching, multi-beat nodes, every character used, solvable puzzles. The old checks parsed raw
Ren'Py with regex; these walk the structured `nodes`/`places` components directly (a graph walk
over end-targets / move-actions, len(lines), declared item scans) — no engine source involved.
Reference integrity (speakers, jump/talk/item targets) is owned by ir_crossref inside `compiles`.

Each check has the validate signature (artifact, check, run_dir) -> (ok, detail).
register_all() wires them into maestro.validate plus the spec-baseline normalizer.
"""

from typing import Dict, List, Optional, Tuple

from maestro.validate import register_check

CheckResult = Tuple[bool, Optional[str]]


# ── nodes (shared by VN and PnC dialogue) ────────────────────────────────────

def _nodes(artifact: Dict):
    ns = artifact.get("nodes", {}) or {}
    return ns.get("node_ids", []) or [], ns.get("nodes", {}) or {}


def _node_targets(node: Dict) -> List[str]:
    end = node.get("end", {}) or {}
    if end.get("type") == "jump":
        return [end["target"]] if end.get("target") else []
    if end.get("type") == "menu":
        return [c["target"] for c in end.get("choices", []) if c.get("target")]
    return []


def _reachable(ids: List[str], edges: Dict[str, List[str]]) -> set:
    if not ids:
        return set()
    reachable, frontier = {ids[0]}, [ids[0]]
    while frontier:
        for tgt in edges.get(frontier.pop(), []):
            if tgt in edges and tgt not in reachable:
                reachable.add(tgt)
                frontier.append(tgt)
    return reachable


def _references(node_ids: List[str], nodes: Dict) -> List[Tuple[str, str, str]]:
    """Every edge as (source, target, label) — label is the menu choice text or 'continues'."""
    refs: List[Tuple[str, str, str]] = []
    for nid in node_ids:
        end = nodes.get(nid, {}).get("end", {}) or {}
        kind = end.get("type")
        if kind == "jump" and end.get("target"):
            refs.append((nid, end["target"], "continues"))
        elif kind == "menu":
            for c in end.get("choices", []) or []:
                if c.get("target"):
                    refs.append((nid, c["target"], c.get("text") or "(choice)"))
    return refs


def _shortest_path(entry: str, target: str, edges: Dict[str, List[str]]) -> List[str]:
    """BFS the id path entry→target over written edges. [] if unreachable."""
    if entry == target:
        return [entry]
    prev: Dict[str, Optional[str]] = {entry: None}
    frontier = [entry]
    while frontier:
        nxt = []
        for n in frontier:
            for t in edges.get(n, []):
                if t not in prev:
                    prev[t] = n
                    if t == target:
                        path = [t]
                        while prev[path[-1]] is not None:
                            path.append(prev[path[-1]])  # type: ignore[arg-type]
                        return list(reversed(path))
                    nxt.append(t)
        frontier = nxt
    return []


def node_view(artifact: Dict) -> Dict:
    node_ids, nodes = _nodes(artifact)
    synopses = (artifact.get("nodes", {}) or {}).get("synopses", {}) or {}
    edges = {nid: _node_targets(nodes.get(nid, {})) for nid in node_ids}
    reachable = _reachable(node_ids, edges)
    written = set(node_ids)
    entry = node_ids[0] if node_ids else None

    # An OPEN SLOT is a target a written node already points at but that does not exist yet —
    # the only place a new node may legitimately go. Each carries the path that leads to it
    # (ancestor synopses) so the author continues the story instead of re-treading a sibling.
    slots: Dict[str, Dict] = {}
    for src, tgt, label in _references(node_ids, nodes):
        if tgt in written:
            continue
        slots.setdefault(tgt, {"id": tgt, "from": []})["from"].append(
            {"node": src, "label": label})
    for tgt, slot in slots.items():
        parent = slot["from"][0]["node"]
        path_ids = _shortest_path(entry, parent, edges) if entry else []
        slot["path"] = [{"id": pid, "synopsis": synopses.get(pid, "")} for pid in path_ids]

    beats = [b.get("id") for b in (artifact.get("outline", {}) or {}).get("beats", []) if b.get("id")]
    covered = {nodes.get(nid, {}).get("beat") for nid in node_ids}
    return {
        "node_ids": node_ids,
        "edges": {nid: sorted(set(e)) for nid, e in edges.items()},
        "reachable": sorted(reachable),
        "unreachable": [n for n in node_ids if n not in reachable],
        "line_counts": {nid: len(nodes.get(nid, {}).get("lines", [])) for nid in node_ids},
        "synopses": synopses,
        "open_slots": sorted(slots.values(), key=lambda s: s["id"]),
        "beats_todo": [b for b in beats if b not in covered],
    }


def check_reachable_from_start(artifact: Dict, check: Dict, run_dir) -> CheckResult:
    node_ids, nodes = _nodes(artifact)
    if not node_ids:
        return False, "no nodes to reach"
    edges = {nid: _node_targets(nodes.get(nid, {})) for nid in node_ids}
    reachable = _reachable(node_ids, edges)
    orphans = [n for n in node_ids if n not in reachable]
    if orphans:
        return False, f"nodes unreachable from '{node_ids[0]}': {orphans[:5]}"
    return True, None


def check_node_targets_resolve(artifact: Dict, check: Dict, run_dir) -> CheckResult:
    """Every jump/menu target points at a node that actually exists. Node reference integrity is
    otherwise only caught by ir_crossref at `compiles` — which, for a navigation game, is owned by
    `places`, leaving a dangling NODE jump unfixable (the places sub-loop has no node tools). Run it
    here so the error surfaces in NODES mode, where write_node/edit_node can fix it."""
    node_ids, nodes = _nodes(artifact)
    ids = set(node_ids)
    bad = []
    for nid in node_ids:
        for tgt in _node_targets(nodes.get(nid, {})):
            if tgt not in ids:
                bad.append(f"{nid} -> {tgt}")
    if bad:
        return False, (f"node jump/menu targets that don't exist: {bad[:5]} — either create those "
                       f"nodes (write_node) or repoint the jump to an existing node (edit_node).")
    return True, None


def check_min_branches(artifact: Dict, check: Dict, run_dir) -> CheckResult:
    _, nodes = _nodes(artifact)
    n = sum(1 for node in nodes.values() if (node.get("end", {}) or {}).get("type") == "menu")
    need = check.get("min", 1)
    if n < need:
        return False, f"only {n} menu(s), need {need} — add player choices (end.type 'menu')"
    return True, None


def check_each_node_min_lines(artifact: Dict, check: Dict, run_dir) -> CheckResult:
    node_ids, nodes = _nodes(artifact)
    need = check.get("min", 3)
    thin = [f"{nid} ({len(nodes.get(nid, {}).get('lines', []))})"
            for nid in node_ids if len(nodes.get(nid, {}).get("lines", [])) < need]
    if thin:
        return False, f"nodes with < {need} lines: {thin[:5]} — give them more beats"
    return True, None


def check_each_node_has_location(artifact: Dict, check: Dict, run_dir) -> CheckResult:
    """Every node names a `location` (a background asset id) — so no scene, least of all the
    opening, renders with a blank background. The skeleton says to tag every node, but nothing
    enforced it, so an untagged node slipped through to a bg-less scene."""
    node_ids, nodes = _nodes(artifact)
    missing = [nid for nid in node_ids if not nodes.get(nid, {}).get("location")]
    if missing:
        return False, (f"nodes with no location/background: {missing[:5]} — set each node's "
                       f"`location` to a background id (edit_node location='bg_...').")
    return True, None


def check_beats_realized(artifact: Dict, check: Dict, run_dir) -> CheckResult:
    """Every outline beat is dramatized by at least one scene (a node whose `beat` names it). This
    REPLACES a flat node-count quota: a fixed count rewards padding (filler rooms that funnel back),
    so the loop hit the number by fanning out vignettes instead of realizing the arc. Anchoring nodes
    to beats makes the outline the spine — you're done when the planned story is told, not at N nodes."""
    beats = [b.get("id") for b in (artifact.get("outline", {}) or {}).get("beats", []) if b.get("id")]
    if not beats:
        return True, None  # no outline (NPC dialogue) — nothing to realize
    _, nodes = _nodes(artifact)
    covered = {n.get("beat") for n in nodes.values() if n.get("beat")}
    missing = [b for b in beats if b not in covered]
    if missing:
        return False, (f"outline beats with no scene yet: {missing} — write a node that dramatizes "
                       f"each (set the node's `beat` to that beat id). The outline (LOCKED COMPONENTS) "
                       f"holds what each beat is; every beat needs at least one scene.")
    return True, None


def _node_effects(node: Dict) -> List[Dict]:
    """Every effect a node fires — on its lines and on its menu choices."""
    effs = list(e for ln in node.get("lines", []) or [] for e in (ln.get("effects") or []))
    end = node.get("end", {}) or {}
    if end.get("type") == "menu":
        for ch in end.get("choices", []) or []:
            effs += ch.get("effects") or []
    return effs


def _effect_targets(eff: Dict) -> set:
    """The flag/variable ids an effect mutates."""
    if not isinstance(eff, dict):
        return set()
    out = {eff[k] for k in ("set_flag", "clear_flag") if eff.get(k)}
    for k in ("set_var", "add_var"):
        sv = eff.get(k)
        if isinstance(sv, dict) and sv.get("var"):
            out.add(sv["var"])
    return out


def _cond_state_refs(cond) -> set:
    """The flag/variable ids a `requires` condition reads (recursing all/any/not)."""
    if not isinstance(cond, dict):
        return set()
    refs = {cond[k] for k in ("var", "flag") if cond.get(k)}
    for key in ("all", "any"):
        for c in cond.get(key, []) or []:
            refs |= _cond_state_refs(c)
    if "not" in cond:
        refs |= _cond_state_refs(cond["not"])
    return refs


def check_no_dead_gates(artifact: Dict, check: Dict, run_dir) -> CheckResult:
    """State is OPTIONAL, but it must not be broken. If a menu choice is gated by `requires` on a
    var/flag, that state must be raised by an effect on a DIFFERENT (earlier) node — otherwise the
    gate can never open at runtime and the branch is dead (the trap we hit: a choice gated on
    trust>=2 whose only +1 is on that same gated choice, so trust is 0 at the gate forever). A game
    with NO gated choices passes trivially — a lean arc whose endings are earned narratively (by the
    beats), not mechanically, is fine. The escape from a bad gate is always a one-edit drop of the
    `requires`, so this can't spiral the loop."""
    _, nodes = _nodes(artifact)
    set_in: Dict[str, set] = {}
    for nid, node in nodes.items():
        for eff in _node_effects(node):
            for t in _effect_targets(eff):
                set_in.setdefault(t, set()).add(nid)
    dead = []
    for nid, node in nodes.items():
        end = node.get("end", {}) or {}
        if end.get("type") != "menu":
            continue
        for ch in end.get("choices", []) or []:
            for ref in _cond_state_refs(ch.get("requires")):
                if not (set_in.get(ref, set()) - {nid}):  # only set here (or never) → unopenable
                    dead.append(f"{nid} (gates on '{ref}')")
    if dead:
        return False, (f"choices gated on state that is never raised in an earlier scene: {dead[:5]} "
                       f"— the gate can't open, so the branch is dead. Either raise it with an effect "
                       f"in an EARLIER node (add_var/set_flag), or DROP the `requires` so the choice "
                       f"is always available (endings can be earned by the story, not a variable).")
    return True, None


def check_all_characters_speak(artifact: Dict, check: Dict, run_dir) -> CheckResult:
    chars = {c.get("id") for c in artifact.get("premise", {}).get("characters", []) if c.get("id")}
    if not chars:
        return False, "premise has no characters"
    _, nodes = _nodes(artifact)
    spoke = {ln.get("speaker") for node in nodes.values()
             for ln in node.get("lines", []) if ln.get("speaker")}
    silent = sorted(chars - spoke)
    if silent:
        return False, f"characters who never speak: {silent} — give them lines"
    return True, None


# ── places (point-and-click) ─────────────────────────────────────────────────

def _places(artifact: Dict):
    pc = artifact.get("places", {}) or {}
    return pc.get("place_ids", []) or [], pc.get("places", {}) or {}, pc


def _move_targets(place: Dict) -> List[str]:
    return [a["target"] for h in place.get("interactables", [])
            if (a := h.get("action", {}) or {}).get("type") == "move" and a.get("target")]


def _cond_items(cond) -> set:
    if not isinstance(cond, dict):
        return set()
    if "item" in cond:
        return {cond["item"]}
    out = set()
    if "not" in cond:
        out |= _cond_items(cond["not"])
    for k in ("all", "any"):
        for c in cond.get(k, []) or []:
            out |= _cond_items(c)
    return out


def _action_conditions(action: Dict):
    t = action.get("type")
    if t in ("move", "win") and action.get("requires"):
        yield action["requires"]
    if t == "use":
        for cl in action.get("clauses", []):
            if cl.get("requires"):
                yield cl["requires"]


def _action_effects(action: Dict):
    if action.get("type") == "use":
        for cl in action.get("clauses", []):
            yield from (cl.get("outcome", {}) or {}).get("effects", []) or []
        yield from (action.get("fallback", {}) or {}).get("effects", []) or []


def _all_actions(places_map: Dict):
    for place in places_map.values():
        for h in place.get("interactables", []):
            yield h.get("action", {}) or {}


def _reachable_places(place_ids, places_map, start):
    if not place_ids:
        return set()
    start = start or place_ids[0]
    reachable, frontier = {start}, [start]
    while frontier:
        for tgt in _move_targets(places_map.get(frontier.pop(), {})):
            if tgt in places_map and tgt not in reachable:
                reachable.add(tgt)
                frontier.append(tgt)
    return reachable


def place_view(artifact: Dict) -> Dict:
    place_ids, places, pc = _places(artifact)
    reachable = _reachable_places(place_ids, places, pc.get("start_place"))
    items = [i.get("id") for i in pc.get("items", []) if i.get("id")]
    taken = {a["item"] for a in _all_actions(places) if a.get("type") == "take" and a.get("item")}
    used = set().union(*(_cond_items(c) for a in _all_actions(places)
                         for c in _action_conditions(a))) if places else set()
    return {
        "place_ids": place_ids,
        "edges": {pid: sorted(set(_move_targets(places.get(pid, {})))) for pid in place_ids},
        "reachable": sorted(reachable),
        "unreachable": [p for p in place_ids if p not in reachable],
        "interactable_counts": {pid: len(places.get(pid, {}).get("interactables", []))
                                for pid in place_ids},
        "items": items,
        "items_never_taken": [i for i in items if i not in taken],
        "items_never_used": [i for i in items if i not in used],
    }


def check_places_reachable(artifact: Dict, check: Dict, run_dir) -> CheckResult:
    place_ids, places, pc = _places(artifact)
    if not place_ids:
        return False, "no places to reach"
    reachable = _reachable_places(place_ids, places, pc.get("start_place"))
    orphans = [p for p in place_ids if p not in reachable]
    if orphans:
        srcs = sorted(reachable)[:3] or [pc.get("start_place")]
        return False, (
            f"places unreachable from start: {orphans[:5]}. In a REACHABLE place (one of {srcs}) "
            f"ADD a NEW move hotspot pointing AT the orphan — do NOT repoint an existing hotspot "
            f"(that breaks its current route). e.g. add_interactable(place_id=\"{srcs[0]}\", "
            f'interactable={{"id":"h_to_{orphans[0]}","label":"<exit>",'
            f'"position":{{"rect":{{"x":1040,"y":560,"w":180,"h":120}}}},'
            f'"action":{{"type":"move","target":"{orphans[0]}"}}}}). '
            f"The move must live in a REACHABLE place and point AT the orphan, not the reverse.")
    return True, None


def check_each_place_min_interactables(artifact: Dict, check: Dict, run_dir) -> CheckResult:
    place_ids, places, _ = _places(artifact)
    need = check.get("min", 2)
    thin = [f"{pid} ({len(places.get(pid, {}).get('interactables', []))})"
            for pid in place_ids if len(places.get(pid, {}).get("interactables", [])) < need]
    if thin:
        return False, f"places with < {need} interactables: {thin[:5]}"
    return True, None


def check_items_obtainable(artifact: Dict, check: Dict, run_dir) -> CheckResult:
    _, places, pc = _places(artifact)
    items = {i.get("id") for i in pc.get("items", []) if i.get("id")}
    taken = {a["item"] for a in _all_actions(places) if a.get("type") == "take" and a.get("item")}
    missing = sorted(items - taken)
    if missing:
        return False, f"items never obtainable (no take action): {missing} — add a take hotspot"
    return True, None


def check_items_used(artifact: Dict, check: Dict, run_dir) -> CheckResult:
    _, places, pc = _places(artifact)
    items = {i.get("id") for i in pc.get("items", []) if i.get("id")}
    used = set()
    for a in _all_actions(places):
        for c in _action_conditions(a):
            used |= _cond_items(c)
    missing = sorted(items - used)
    if missing:
        return False, f"items never used (no condition gates on them): {missing}"
    return True, None


def check_goal_reachable(artifact: Dict, check: Dict, run_dir) -> CheckResult:
    place_ids, places, pc = _places(artifact)
    goal = pc.get("goal")
    if not isinstance(goal, dict) or not goal.get("id"):
        return False, ("rooms goal must be DECLARED — call set_places_meta(goal={'type':'flag'|"
                       "'room','id':...})")
    reachable = _reachable_places(place_ids, places, pc.get("start_place"))
    if goal.get("type") == "room":
        if goal["id"] not in reachable:
            return False, f"goal place '{goal['id']}' is not reachable"
        return True, None
    # flag goal: a reachable action must set it, and a reachable win action must exist
    set_flags = set()
    has_win = False
    for pid in reachable:
        for h in places.get(pid, {}).get("interactables", []):
            a = h.get("action", {}) or {}
            if a.get("type") == "win":
                has_win = True
            for e in _action_effects(a):
                if e.get("set_flag"):
                    set_flags.add(e["set_flag"])
    srcs = sorted(reachable)[:3] or [pc.get("start_place")]
    if goal["id"] not in set_flags:
        return False, (
            f"the win flag '{goal['id']}' is never set by a reachable hotspot. ADD a `use` hotspot "
            f"in a REACHABLE place ({srcs}) whose outcome sets it — e.g. "
            f'add_interactable(place_id="{srcs[0]}", interactable={{"id":"h_win_{goal["id"]}",'
            f'"label":"<thing>","position":{{"rect":{{"x":520,"y":300,"w":200,"h":160}}}},'
            f'"action":{{"type":"use","clauses":[{{"requires":{{"flag":"<some flag>"}},'
            f'"outcome":{{"text":"...","effects":[{{"set_flag":"{goal["id"]}"}}]}}}}],'
            f'"fallback":{{"text":"Not yet."}}}}}}). The set_flag effect is what makes the goal reachable.')
    if not has_win:
        return False, (
            f"no reachable hotspot has a 'win' action. ADD a hotspot whose action is "
            f'{{"type":"win"}} to a REACHABLE place ({srcs}) — e.g. add_interactable(place_id='
            f'"{srcs[0]}", interactable={{"id":"h_finish","label":"<thing>",'
            f'"position":{{"rect":{{"x":540,"y":520,"w":200,"h":120}}}},'
            f'"action":{{"type":"win"}}}}). The win action ends the game once the goal flag is set.')
    return True, None


_CHECKS = {
    "reachable_from_start": check_reachable_from_start,
    "node_targets_resolve": check_node_targets_resolve,
    "min_branches": check_min_branches,
    "each_node_min_lines": check_each_node_min_lines,
    "each_node_has_location": check_each_node_has_location,
    "beats_realized": check_beats_realized,
    "no_dead_gates": check_no_dead_gates,
    "all_characters_speak": check_all_characters_speak,
    "places_reachable": check_places_reachable,
    "each_place_min_interactables": check_each_place_min_interactables,
    "items_obtainable": check_items_obtainable,
    "items_used": check_items_used,
    "goal_reachable": check_goal_reachable,
}


def register_all() -> None:
    for name, fn in _CHECKS.items():
        register_check(name, fn)
    # Guarantee a spec's baseline done-conditions exist regardless of what the proposer drafted.
    from maestro.spec_tools import register_spec_normalizer
    from renpy.spec_baseline import enforce_baseline
    register_spec_normalizer(enforce_baseline)
