"""Point-and-click structure checks.

The generic checks (exists/count/refs_resolve/compiles) can't express "is this a
playable adventure" — that needs reading the room graph and the Ren'Py logic the
agent wrote into each hotspot. These checks parse the `rooms` component so a spec can
demand a real game: every room reachable by clicking, every item obtainable and used,
the goal actually winnable, hotspots on-canvas.

The interaction graph lives in the hotspot `logic` bodies (plain Ren'Py): `jump room_x`
moves rooms, `inventory.append("item_x")` grants an item, `"item_x" in inventory` gates
a use, `<flag> = True` sets a puzzle flag. Each check fn has the validate signature
(artifact, check, run_dir) -> (ok, detail). register_all() (renpy.checks) wires them in.
"""

import re
from typing import Dict, List, Tuple

from maestro.validate import register_check

CheckResult = Tuple[bool, object]

_CANVAS_W, _CANVAS_H = 1280, 720

_JUMP_RE = re.compile(r'\bjump\s+(\w+)')
_APPEND_RE = re.compile(r'inventory\.append\(\s*[\'"](\w+)[\'"]')
_USE_RE = re.compile(r'[\'"](\w+)[\'"]\s*(?:not\s+)?in\s+inventory')
_FLAG_SET_RE = re.compile(r'(\w+)\s*=\s*True\b')


def _rooms(artifact: Dict) -> Dict:
    return artifact.get("rooms", {}) or {}


def _room_map(artifact: Dict) -> Tuple[List[str], Dict[str, Dict], str]:
    r = _rooms(artifact)
    room_ids = r.get("room_ids", []) or []
    rooms = r.get("rooms", {}) or {}
    start = r.get("start_room") or (room_ids[0] if room_ids else "")
    return room_ids, rooms, start


def _room_logic(room: Dict) -> str:
    """All hotspot logic bodies in a room, concatenated — the room's outgoing behaviour."""
    return "\n".join(h.get("logic", "") for h in room.get("hotspots", []) if isinstance(h, dict))


def _all_logic(artifact: Dict) -> str:
    _, rooms, _ = _room_map(artifact)
    return "\n".join(_room_logic(room) for room in rooms.values() if isinstance(room, dict))


def _reachable(artifact: Dict) -> set:
    room_ids, rooms, start = _room_map(artifact)
    if not start or start not in rooms:
        return set()
    reachable, frontier = {start}, [start]
    while frontier:
        cur = frontier.pop()
        for tgt in _JUMP_RE.findall(_room_logic(rooms.get(cur, {}))):
            if tgt in rooms and tgt not in reachable:
                reachable.add(tgt)
                frontier.append(tgt)
    return reachable


def room_view(artifact: Dict) -> Dict:
    """Compact map of the room graph for the build agent's context — real ids, the exact
    `jump room_x` edges to repoint, which rooms are still cut off, and item coverage."""
    room_ids, rooms, start = _room_map(artifact)
    reachable = _reachable(artifact)
    all_logic = _all_logic(artifact)
    item_ids = [it.get("id") for it in _rooms(artifact).get("items", []) if isinstance(it, dict)]
    obtained = set(_APPEND_RE.findall(all_logic))
    used = set(_USE_RE.findall(all_logic))
    return {
        "room_ids": room_ids,
        "start_room": start,
        "edges": {rid: sorted(set(_JUMP_RE.findall(_room_logic(rooms.get(rid, {})))))
                  for rid in room_ids},
        "hotspot_counts": {rid: len(rooms.get(rid, {}).get("hotspots", [])) for rid in room_ids},
        "reachable": sorted(reachable),
        "unreachable": [r for r in room_ids if r not in reachable],
        "items": item_ids,
        "items_never_taken": [i for i in item_ids if i not in obtained],
        "items_never_used": [i for i in item_ids if i not in used],
    }


def check_rooms_reachable(artifact: Dict, check: Dict, run_dir) -> CheckResult:
    room_ids, rooms, start = _room_map(artifact)
    if not room_ids:
        return False, "no rooms to reach"
    if start not in rooms:
        return False, f"start_room {start!r} has no room entry yet"
    orphans = [r for r in room_ids if r not in _reachable(artifact)]
    if orphans:
        return False, f"rooms unreachable by clicking from '{start}': {orphans[:5]} — add a hotspot that `jump`s to them"
    return True, None


def check_each_room_min_hotspots(artifact: Dict, check: Dict, run_dir) -> CheckResult:
    room_ids, rooms, _ = _room_map(artifact)
    need = check.get("min", 2)
    thin = []
    for rid in room_ids:
        n = len(rooms.get(rid, {}).get("hotspots", []))
        if n < need:
            thin.append(f"{rid} ({n})")
    if thin:
        return False, f"rooms with < {need} hotspots: {thin[:5]}"
    return True, None


def check_hotspots_in_bounds(artifact: Dict, check: Dict, run_dir) -> CheckResult:
    _, rooms, _ = _room_map(artifact)
    bad = []
    for rid, room in rooms.items():
        for h in room.get("hotspots", []):
            rect = h.get("rect")
            if not (isinstance(rect, list) and len(rect) == 4):
                bad.append(f"{rid}/{h.get('id')}: rect not [x,y,w,h]")
                continue
            x, y, w, hh = rect
            if w <= 0 or hh <= 0 or x < 0 or y < 0 or x + w > _CANVAS_W or y + hh > _CANVAS_H:
                bad.append(f"{rid}/{h.get('id')}: {rect}")
    if bad:
        return False, f"hotspots off-canvas/degenerate (frame is {_CANVAS_W}x{_CANVAS_H}): {bad[:5]}"
    return True, None


def check_items_obtainable(artifact: Dict, check: Dict, run_dir) -> CheckResult:
    items = [it.get("id") for it in _rooms(artifact).get("items", []) if isinstance(it, dict)]
    obtained = set(_APPEND_RE.findall(_all_logic(artifact)))
    missing = [i for i in items if i not in obtained]
    if missing:
        return False, f"items never granted by any hotspot: {missing[:5]} — add a take hotspot (inventory.append)"
    return True, None


def check_items_used(artifact: Dict, check: Dict, run_dir) -> CheckResult:
    items = [it.get("id") for it in _rooms(artifact).get("items", []) if isinstance(it, dict)]
    used = set(_USE_RE.findall(_all_logic(artifact)))
    dead = [i for i in items if i not in used]
    if dead:
        return False, f"items never used (dead inventory): {dead[:5]} — gate a hotspot on `\"item\" in inventory`"
    return True, None


def check_goal_reachable(artifact: Dict, check: Dict, run_dir) -> CheckResult:
    goal = _rooms(artifact).get("goal") or {}
    gtype, gid = goal.get("type"), goal.get("id")
    if not gtype or not gid:
        return False, "rooms.goal must declare {type: flag|room, id}"
    reachable = _reachable(artifact)
    if gtype == "room":
        if gid not in reachable:
            return False, f"goal room {gid!r} not reachable by clicking"
        return True, None
    # flag goal: the winning flag must be set inside a reachable room, and a win must fire.
    _, rooms, _ = _room_map(artifact)
    sets_goal = any(gid in _FLAG_SET_RE.findall(_room_logic(rooms.get(rid, {}))) for rid in reachable)
    if not sets_goal:
        return False, f"goal flag {gid!r} is never set (`$ {gid} = True`) in a reachable room"
    if "win" not in _JUMP_RE.findall(_all_logic(artifact)):
        return False, "no hotspot ever `jump win` — the win is unreachable even once the goal flag is set"
    return True, None


_CHECKS = {
    "rooms_reachable": check_rooms_reachable,
    "each_room_min_hotspots": check_each_room_min_hotspots,
    "hotspots_in_bounds": check_hotspots_in_bounds,
    "items_obtainable": check_items_obtainable,
    "items_used": check_items_used,
    "goal_reachable": check_goal_reachable,
}


def register_all() -> None:
    for name, fn in _CHECKS.items():
        register_check(name, fn)
