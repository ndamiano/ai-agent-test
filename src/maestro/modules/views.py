"""Engine-neutral graph walks over the Game IR components: the low-level helpers the structural
checks share, plus `node_view` / `place_view` — the compact projections the build loop hands a
content module so it sees what already exists and where the next item must go (the open slot).
"""

from typing import Dict, List, Optional, Tuple


# ── nodes ─────────────────────────────────────────────────────────────────────
def nodes_of(artifact: Dict):
    ns = artifact.get("nodes", {}) or {}
    return ns.get("node_ids", []) or [], ns.get("nodes", {}) or {}


def node_targets(node: Dict) -> List[str]:
    end = node.get("end", {}) or {}
    if end.get("type") == "jump":
        return [end["target"]] if end.get("target") else []
    if end.get("type") == "menu":
        return [c["target"] for c in end.get("choices", []) if c.get("target")]
    return []


def reachable(ids: List[str], edges: Dict[str, List[str]]) -> set:
    if not ids:
        return set()
    seen, frontier = {ids[0]}, [ids[0]]
    while frontier:
        for tgt in edges.get(frontier.pop(), []):
            if tgt in edges and tgt not in seen:
                seen.add(tgt)
                frontier.append(tgt)
    return seen


def references(node_ids: List[str], nodes: Dict) -> List[Tuple[str, str, str]]:
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


def shortest_path(entry: str, target: str, edges: Dict[str, List[str]]) -> List[str]:
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
    node_ids, nodes = nodes_of(artifact)
    synopses = (artifact.get("nodes", {}) or {}).get("synopses", {}) or {}
    edges = {nid: node_targets(nodes.get(nid, {})) for nid in node_ids}
    reach = reachable(node_ids, edges)
    written = set(node_ids)
    entry = node_ids[0] if node_ids else None

    # An OPEN SLOT is a target a written node already points at but that does not exist yet — the
    # only place a new node may legitimately go. Each carries the path that leads to it (ancestor
    # synopses) so the author continues the story instead of re-treading a sibling.
    slots: Dict[str, Dict] = {}
    for src, tgt, label in references(node_ids, nodes):
        if tgt in written:
            continue
        slots.setdefault(tgt, {"id": tgt, "from": []})["from"].append({"node": src, "label": label})
    beats_full = [b for b in (artifact.get("outline", {}) or {}).get("beats", []) if b.get("id")]
    beat_ids = [b["id"] for b in beats_full]
    beat_index = {bid: i for i, bid in enumerate(beat_ids)}
    covered = {nodes.get(nid, {}).get("beat") for nid in node_ids}
    uncovered = [b for b in beat_ids if b not in covered]

    for tgt, slot in slots.items():
        parent = slot["from"][0]["node"]
        path_ids = shortest_path(entry, parent, edges) if entry else []
        slot["path"] = [{"id": pid, "synopsis": synopses.get(pid, "")} for pid in path_ids]
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


# ── places ────────────────────────────────────────────────────────────────────
def places_of(artifact: Dict):
    pc = artifact.get("places", {}) or {}
    return pc.get("place_ids", []) or [], pc.get("places", {}) or {}, pc


def move_targets(place: Dict) -> List[str]:
    return [a["target"] for h in place.get("interactables", [])
            if (a := h.get("action", {}) or {}).get("type") == "move" and a.get("target")]


def cond_items(cond) -> set:
    if not isinstance(cond, dict):
        return set()
    if "item" in cond:
        return {cond["item"]}
    out = set()
    if "not" in cond:
        out |= cond_items(cond["not"])
    for k in ("all", "any"):
        for c in cond.get(k, []) or []:
            out |= cond_items(c)
    return out


def action_conditions(action: Dict):
    t = action.get("type")
    if t in ("move", "win") and action.get("requires"):
        yield action["requires"]
    if t == "use":
        for cl in action.get("clauses", []):
            if cl.get("requires"):
                yield cl["requires"]


def action_effects(action: Dict):
    if action.get("type") == "use":
        for cl in action.get("clauses", []):
            yield from (cl.get("outcome", {}) or {}).get("effects", []) or []
        yield from (action.get("fallback", {}) or {}).get("effects", []) or []


def all_actions(places_map: Dict):
    for place in places_map.values():
        for h in place.get("interactables", []):
            yield h.get("action", {}) or {}


def reachable_places(place_ids, places_map, start):
    if not place_ids:
        return set()
    start = start or place_ids[0]
    reach, frontier = {start}, [start]
    while frontier:
        for tgt in move_targets(places_map.get(frontier.pop(), {})):
            if tgt in places_map and tgt not in reach:
                reach.add(tgt)
                frontier.append(tgt)
    return reach


def place_view(artifact: Dict) -> Dict:
    place_ids, places, pc = places_of(artifact)
    reach = reachable_places(place_ids, places, pc.get("start_place"))
    items = [i.get("id") for i in pc.get("items", []) if i.get("id")]
    taken = {a["item"] for a in all_actions(places) if a.get("type") == "take" and a.get("item")}
    used = set().union(*(cond_items(c) for a in all_actions(places)
                         for c in action_conditions(a))) if places else set()
    return {
        "place_ids": place_ids,
        "edges": {pid: sorted(set(move_targets(places.get(pid, {})))) for pid in place_ids},
        "reachable": sorted(reach),
        "unreachable": [p for p in place_ids if p not in reach],
        "interactable_counts": {pid: len(places.get(pid, {}).get("interactables", []))
                                for pid in place_ids},
        "items": items,
        "items_never_taken": [i for i in items if i not in taken],
        "items_never_used": [i for i in items if i not in used],
    }
