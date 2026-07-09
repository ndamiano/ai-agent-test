"""Engine-neutral graph walks over the Game IR components — the low-level grammar the modules'
policy checks and projections share (each module owns its own view/projection; these are the
shared primitives they compose).
"""

from typing import Dict, List, Optional, Tuple

# A menu is a dramatic fork, not a location picker. Shared here because BOTH ends of the
# contract read it: scenes' write-time end policy caps a node's menu, and story's write-time
# branch policy caps branches-per-beat so the derived menu (branches + 'continue') always fits.
MAX_MENU_CHOICES = 3


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


def reachable(ids: List[str], edges: Dict[str, List[str]], roots: List[str] = None) -> set:
    if not ids:
        return set()
    start = [r for r in (roots or []) if r in edges] or [ids[0]]
    seen, frontier = set(start), list(start)
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


# ── places ────────────────────────────────────────────────────────────────────
def places_of(artifact: Dict):
    pc = artifact.get("places", {}) or {}
    return pc.get("place_ids", []) or [], pc.get("places", {}) or {}, pc


def move_targets(place: Dict) -> List[str]:
    return [a["target"] for h in place.get("interactables", [])
            if (a := h.get("action", {}) or {}).get("type") == "move" and a.get("target")]


def cond_items(cond) -> set:
    # Tolerant of model-shaped junk: "item" may arrive as a list (or worse) — collect the string
    # refs, never crash the detector; the schema/compile gate owns rejecting the malformed shape.
    if not isinstance(cond, dict):
        return set()
    if "item" in cond:
        it = cond["item"]
        if isinstance(it, str):
            return {it}
        if isinstance(it, list):
            return {x for x in it if isinstance(x, str)}
        return set()
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
        for cl in action.get("clauses", []) or []:
            if isinstance(cl, dict) and cl.get("requires"):
                yield cl["requires"]


def action_effects(action: Dict):
    # Tolerates malformed shapes (string fallback, non-dict outcome): this walks the raw
    # artifact between validated writes, and a crash here kills the whole build process.
    if action.get("type") == "use":
        for cl in action.get("clauses", []) or []:
            if isinstance(cl, dict) and isinstance(cl.get("outcome"), dict):
                yield from cl["outcome"].get("effects", []) or []
        fb = action.get("fallback")
        if isinstance(fb, dict):
            yield from fb.get("effects", []) or []


def all_actions(places_map: Dict):
    for place in places_map.values():
        for h in place.get("interactables", []):
            yield h.get("action", {}) or {}


def reachable_places(place_ids, places_map, start):
    if not place_ids:
        return set()
    # A declared start that was never authored must not seed the walk — the reachability message
    # would tell the model to wire hotspots into a place that doesn't exist.
    start = start if start in places_map else place_ids[0]
    reach, frontier = {start}, [start]
    while frontier:
        for tgt in move_targets(places_map.get(frontier.pop(), {})):
            if tgt in places_map and tgt not in reach:
                reach.add(tgt)
                frontier.append(tgt)
    return reach

