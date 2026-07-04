"""world — clickable rooms/screens. Authors the `places` component.

A point-and-click world: places connected by `move`, hotspots that examine / take / use / talk,
items that are taken and used, and an optional win goal. A slot-guarded sub-loop grows the map one
room at a time, wiring each new place into the reachable graph.

Example games:
  - "escape a locked observatory before dawn"            — cast + world + inventory
  - "wander a night market solving each vendor's problem" — cast + world + scenes
"""

from typing import Dict, List, Optional

from collections import deque

from maestro import context_render as cr
from maestro.modules import checks, views
from maestro.modules.module import Check, Error, Module, register_module

_RPG_KINDS = {"world_map", "town", "interior"}

# A walkable map is a grid of tile CHARS. Each char resolves (via the place's `tiles.legend`, or
# these defaults when it doesn't declare one) to a ROLE — the only thing the engine reads: `open`
# = walkable, `blocked` = wall. Everything else about a tile (what it looks like) is the free `theme`
# string, cosmetic only. So the behavioural vocabulary is tiny and closed; the visual one is open.
_ROLES = {"open", "blocked"}
DEFAULT_LEGEND = {
    ".": {"role": "open", "theme": "ground"},
    ",": {"role": "open", "theme": "path"},
    "#": {"role": "blocked", "theme": "wall"},
    "T": {"role": "blocked", "theme": "tree"},
    "~": {"role": "blocked", "theme": "water"},
    "%": {"role": "blocked", "theme": "rock"},
}


# ── the compact graph projection + presentation block ─────────────────────────
def place_view(artifact: Dict) -> Dict:
    place_ids, places, pc = views.places_of(artifact)
    reach = views.reachable_places(place_ids, places, pc.get("start_place"))
    items = [i.get("id") for i in (artifact.get("items") or {}).get("items", []) if i.get("id")]
    taken = {a["item"] for a in views.all_actions(places) if a.get("type") == "take" and a.get("item")}
    used = set().union(*(views.cond_items(c) for a in views.all_actions(places)
                         for c in views.action_conditions(a))) if places else set()
    return {
        "place_ids": place_ids,
        "edges": {pid: sorted(set(views.move_targets(places.get(pid, {})))) for pid in place_ids},
        "reachable": sorted(reach),
        "unreachable": [p for p in place_ids if p not in reach],
        "interactable_counts": {pid: len(places.get(pid, {}).get("interactables", []))
                                for pid in place_ids},
        "items": items,
        "items_never_taken": [i for i in items if i not in taken],
        "items_never_used": [i for i in items if i not in used],
    }


def places_index_block(artifact: Dict) -> List[str]:
    """The map's shape as prompt context: place ids, start, and each place's hotspot ids —
    never tile rows."""
    pc = artifact.get("places") or {}
    ids = pc.get("place_ids") or []
    if not ids:
        return []
    places = pc.get("places") or {}
    out = ["", f"PLACES (start: {pc.get('start_place')}):"]
    for pid in ids:
        hs = [h.get("id") for h in (places.get(pid, {}) or {}).get("interactables", [])
              if isinstance(h, dict)]
        out.append(f"  {pid} — hotspots: {hs}")
    return out


# ── map policy checks ──────────────────────────────────────────────────────────
def places_reachable(artifact: Dict):
    place_ids, places, pc = views.places_of(artifact)
    if not place_ids:
        return False, "no places to reach"
    reach = views.reachable_places(place_ids, places, pc.get("start_place"))
    orphans = [p for p in place_ids if p not in reach]
    if orphans:
        srcs = sorted(reach)[:3] or [pc.get("start_place")]
        return False, (
            f"places unreachable from start: {orphans[:5]}. In a REACHABLE place (one of {srcs}) "
            f"ADD a NEW move hotspot pointing AT the orphan — do NOT repoint an existing hotspot "
            f"(that breaks its current route). e.g. add_interactable(place_id=\"{srcs[0]}\", "
            f'interactable={{"id":"h_to_{orphans[0]}","label":"<exit>",'
            f'"position":{{"rect":{{"x":1040,"y":560,"w":180,"h":120}}}},'
            f'"action":{{"type":"move","target":"{orphans[0]}"}}}}). '
            f"The move must live in a REACHABLE place and point AT the orphan, not the reverse.")
    return True, None


def each_place_min_interactables(artifact: Dict, *, min=2):
    place_ids, places, _ = views.places_of(artifact)
    thin = [f"{pid} ({len(places.get(pid, {}).get('interactables', []))})"
            for pid in place_ids if len(places.get(pid, {}).get("interactables", [])) < min]
    if thin:
        return False, f"places with < {min} interactables: {thin[:5]}"
    return True, None


def nodes_world_entered(artifact: Dict):
    """When places exist the game STARTS in the world, and the node graph plays only through world
    entry points: talk actions and encounter resolution jumps. Every node must be reachable
    from those entries — otherwise it's authored story the player can never see (both live builds
    shipped their whole opening unreachable; run 2 shipped ALL 12 nodes dead)."""
    node_ids, nodes = views.nodes_of(artifact)
    place_ids, places, _ = views.places_of(artifact)
    if not node_ids or not place_ids:
        return True, None
    entries = set()
    for a in views.all_actions(places):
        if a.get("type") == "talk" and a.get("node"):
            entries.add(a["node"])
    for e in (artifact.get("combat") or {}).get("encounters", []) or []:
        for key in ("on_victory", "on_defeat"):
            ne = e.get(key) if isinstance(e, dict) else None
            if isinstance(ne, dict) and ne.get("type") == "jump" and isinstance(ne.get("target"), str):
                entries.add(ne["target"])
    valid = sorted(e for e in entries if e in nodes)
    if not valid:
        return False, (
            "the dialogue graph is NEVER entered — no place interactable has a talk action and no "
            "encounter resolves into a node, so none of the authored scenes can play. Wire an "
            'entry: give an interactable a talk action ({"type":"talk","node":"<the opening '
            'scene id>"}), and/or give an encounter an on_victory {"type":"jump","target":"<a '
            'node id>"}.')
    edges = {nid: views.node_targets(nodes.get(nid, {})) for nid in node_ids}
    seen = {v for v in valid}
    frontier = list(seen)
    while frontier:
        for t in edges.get(frontier.pop(), []):
            if t in edges and t not in seen:
                seen.add(t)
                frontier.append(t)
    dead = [n for n in node_ids if n not in seen]
    if dead:
        return False, (
            f"scenes the player can NEVER reach through play: {dead[:6]} — the world enters the "
            f"dialogue only at {valid[:4]}. Add a talk hotspot pointing at the first dead scene "
            f"(add_interactable), or repoint an entered scene's end/menu to lead into them "
            f"(edit_node).")
    return True, None


# ── write-time action policy (what add_interactable/edit_place enforce) ────────
def _ir_defs():
    import json
    from pathlib import Path
    return json.loads((Path(__file__).resolve().parents[3] / "docs" / "game_ir.schema.json")
                      .read_text(encoding="utf-8"))["$defs"]


def _action_validator():
    import jsonschema
    return jsonschema.Draft202012Validator({"$ref": "#/$defs/action", "$defs": _ir_defs()})


def _action_shapes() -> Dict:
    """type -> (allowed keys, required keys), read from the schema's per-type action defs."""
    shapes = {}
    for name, d in _ir_defs().items():
        if name.startswith("action_"):
            props = d.get("properties", {})
            t = (props.get("type", {}) or {}).get("const") or name[len("action_"):]
            shapes[t] = (set(props), set(d.get("required", [])))
    return shapes


_ACTION_VALIDATOR = None
_ACTION_SHAPES = None


def action_error(action) -> Optional[str]:
    """Reject a malformed interactable action at write time with an ACTIONABLE message. Names the
    wrong/missing KEY before the oneOf backstop — its 'not valid under any of the given schemas'
    names nothing, and a small model retried the same bad key 26 steps straight against it."""
    if not isinstance(action, dict) or not action.get("type"):
        return "action needs an object with a 'type'"
    if action.get("type") == "use":
        for i, cl in enumerate(action.get("clauses", []) or []):
            req = cl.get("requires") if isinstance(cl, dict) else None
            if not isinstance(req, dict) or not req:
                return (f"use clause[{i}].requires must be a REAL condition — e.g. "
                        f'{{"flag":"x"}}, {{"item":"y"}}, or {{"var":"g","op":">=","value":10}}. '
                        f"For an outcome that ALWAYS fires, DROP the clause and put it in `fallback`: "
                        f'{{"type":"use","fallback":{{"text":"...","effects":[...]}}}}.')
    global _ACTION_VALIDATOR, _ACTION_SHAPES
    if _ACTION_SHAPES is None:
        _ACTION_SHAPES = _action_shapes()
    t = action["type"]
    if t not in _ACTION_SHAPES:
        return f"unknown action type {t!r} — one of {sorted(_ACTION_SHAPES)}"
    allowed, required = _ACTION_SHAPES[t]
    unknown = sorted(set(action) - allowed)
    if unknown:
        return (f"a {t!r} action does not take {unknown} — its keys are {sorted(allowed)} "
                f"(required: {sorted(required)})")
    missing = sorted(required - set(action))
    if missing:
        return f"a {t!r} action requires {missing} — its keys are {sorted(allowed)}"
    if _ACTION_VALIDATOR is None:
        _ACTION_VALIDATOR = _action_validator()
    errs = sorted(_ACTION_VALIDATOR.iter_errors(action), key=lambda e: len(list(e.path)))
    if errs:
        loc = "/".join(str(p) for p in errs[0].path) or "action"
        return f"action invalid at {loc}: {errs[0].message}"
    return None


def _cell_xy(pos) -> Optional[tuple]:
    """The (x, y) of a {cell:{x,y}} position, or None if it isn't an integer tile."""
    if isinstance(pos, dict) and isinstance(pos.get("cell"), dict):
        cell = pos["cell"]
        if isinstance(cell.get("x"), int) and isinstance(cell.get("y"), int):
            return (cell["x"], cell["y"])
    return None


def _tiles_dims_walls(pid: str, place: Dict):
    """Validate a walkable place's `tiles` block and derive its geometry. Returns
    (w, h, walls_set) on success, or (None, None, error_str) — dimensions come from the rows (any
    size), walls from every char whose legend role is 'blocked'. A small model authors the map as
    ASCII rows it can SEE, not a coordinate soup, so the layout it produces is actually coherent."""
    tiles = place.get("tiles")
    if not isinstance(tiles, dict):
        return None, None, (
            f"places[{pid!r}] is a walkable {place.get('kind')} map — it needs a "
            f"'tiles': {{'legend': {{..}}, 'rows': [\"..\"]}} block: paint the map as rows of tile "
            f"chars, one string per grid row")
    rows = tiles.get("rows")
    if not isinstance(rows, list) or not rows or not all(isinstance(r, str) and r for r in rows):
        return None, None, (f"places[{pid!r}].tiles.rows must be a non-empty list of non-empty "
                            f"strings — one string per row of the map")
    w, h = len(rows[0]), len(rows)
    if any(len(r) != w for r in rows):
        return None, None, (f"places[{pid!r}].tiles.rows are ragged — every row must be the SAME "
                            f"length (row 0 is {w} wide); pad the short rows so the grid is rectangular")
    legend = tiles.get("legend", {})
    if not isinstance(legend, dict):
        return None, None, (f"places[{pid!r}].tiles.legend must be an object mapping a single tile "
                            f"char -> {{'role': 'open'|'blocked', 'theme': '<look>'}}")
    merged = dict(DEFAULT_LEGEND)
    for ch, spec in legend.items():
        if not isinstance(ch, str) or len(ch) != 1:
            return None, None, f"places[{pid!r}].tiles.legend key {ch!r} must be a single character"
        if not isinstance(spec, dict) or spec.get("role") not in _ROLES:
            return None, None, (f"places[{pid!r}].tiles.legend[{ch!r}] needs a 'role' of 'open' "
                                f"(walkable) or 'blocked' (a wall/obstacle)")
        merged[ch] = spec
    walls = set()
    for y, row in enumerate(rows):
        for x, ch in enumerate(row):
            if ch not in merged:
                return None, None, (
                    f"places[{pid!r}].tiles row {y} uses char {ch!r} with no legend entry — declare "
                    f"it in tiles.legend as {{'role':.., 'theme':..}}, or use a default char "
                    f"({' '.join(sorted(DEFAULT_LEGEND))})")
            if merged[ch]["role"] == "blocked":
                walls.add((x, y))
    return w, h, walls


def _reachable(free: set, sources: list) -> set:
    """4-neighbour flood-fill over the free (non-wall, in-bounds) tiles from the entry tiles."""
    seen = {s for s in sources if s in free}
    dq = deque(seen)
    while dq:
        x, y = dq.popleft()
        for nxt in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
            if nxt in free and nxt not in seen:
                seen.add(nxt)
                dq.append(nxt)
    return seen


def _rpg_world_error(c: Dict) -> Optional[str]:
    """Spatial invariants a walkable (world_map/town/interior) map must hold so it actually plays:
    a valid tile grid, in-bounds non-overlapping interactables, none on a blocked tile, and — the
    load-bearing one — every interactable tile WALKABLE-reachable from where the avatar arrives. A
    small model can't be trusted to lay this out; the loop self-corrects off these messages.
    Collects ALL independent problems and reports them together — one at a time makes the model
    ping-pong (fix one, re-introduce another)."""
    places = c.get("places") or {}
    start_place = c.get("start_place")
    issues: List[str] = []

    # Where the avatar arrives in each place: the start spawn + every move that carries a spawn.
    # Each entry keeps WHERE it was declared — an arrival-spawn defect is fixed on the SOURCE move
    # hotspot, and a message that names only the destination zone sends the model rewriting the
    # wrong place (live-build thrash: five rewrites of the destination, zero of the move).
    entries: Dict[str, list] = {}
    start_spawn = _cell_xy(c.get("start_spawn"))
    if start_place and start_spawn:
        entries.setdefault(start_place, []).append(
            (start_spawn, "set_places_meta(start_spawn=...)"))
    for src_pid, place in places.items():
        if not isinstance(place, dict):
            continue
        for h in place.get("interactables") or []:
            act = h.get("action") if isinstance(h, dict) else None
            if isinstance(act, dict) and act.get("type") == "move" and act.get("target"):
                sp = _cell_xy(act.get("spawn"))
                if sp:
                    entries.setdefault(act["target"], []).append(
                        (sp, f"the move interactable {h.get('id')!r} in place {src_pid!r}"))

    for pid, place in places.items():
        if not isinstance(place, dict) or place.get("kind") not in _RPG_KINDS:
            continue
        w, h, walls = _tiles_dims_walls(pid, place)
        if w is None:
            issues.append(walls)  # error string
            continue

        used: Dict[tuple, str] = {}
        for h_ in place.get("interactables") or []:
            iid = h_.get("id")
            xy = _cell_xy(h_.get("position"))
            if xy is None:
                issues.append(f"places[{pid!r}].interactables[{iid!r}] needs a tile position "
                              f"'position': {{'cell': {{'x': int, 'y': int}}}} — this is a walkable map")
                continue
            if not (0 <= xy[0] < w and 0 <= xy[1] < h):
                issues.append(f"places[{pid!r}].interactables[{iid!r}] tile {xy} is outside the "
                              f"{w}x{h} grid")
                continue
            if xy in walls:
                issues.append(f"places[{pid!r}].interactables[{iid!r}] sits on a blocked tile {xy} — "
                              f"the avatar can't stand there; move it onto an open tile or make that "
                              f"tile open")
                continue
            if xy in used:
                issues.append(f"places[{pid!r}] puts two interactables on tile {xy} ({used[xy]} and "
                              f"{iid}) — give each its own tile")
                continue
            used[xy] = iid
            act = h_.get("action") or {}
            if act.get("type") == "move" and act.get("target") in places \
                    and places[act["target"]].get("kind") in _RPG_KINDS \
                    and _cell_xy(act.get("spawn")) is None:
                sp = act.get("spawn")
                feat = (sp or {}).get("feature") if isinstance(sp, dict) else None
                if feat:
                    # A layout-authored arrival resolves the moment the target zone is written
                    # (write_place runs the resolver both directions); an unresolved one here
                    # means the target's layout never declared that feature — actionable.
                    tgt_anchors = places[act["target"]].get("anchors") or {}
                    issues.append(
                        f"places[{pid!r}].interactables[{iid!r}] arrives at feature {feat!r} "
                        f"but {act['target']!r} declares no such feature/exit — use one of "
                        f"{sorted(tgt_anchors) or ['(target has no layout anchors)']}")
                else:
                    issues.append(
                        f"places[{pid!r}].interactables[{iid!r}] moves to walkable place "
                        f"{act['target']!r} but has no 'spawn' — declare the arrival as "
                        f"{{'feature': '<a feature/exit id in {act['target']}>'}} (layout "
                        f"zones) or {{'cell': {{'x','y'}}}}")

        sources = entries.get(pid, [])
        if not sources:
            if pid == start_place:
                issues.append(f"places[{pid!r}] is the walkable start place — declare where the "
                              f"player spawns with set_places_meta(start_spawn="
                              f"{{'cell': {{'x': .., 'y': ..}}}})")
            continue  # an orphan (no entry): places_reachable owns that
        good_sources = []
        for sp, src in sources:
            if not (0 <= sp[0] < w and 0 <= sp[1] < h):
                issues.append(f"the arrival spawn {sp} declared on {src} is outside "
                              f"{pid!r}'s {w}x{h} grid — fix the spawn on THAT declaration (an open "
                              f"in-bounds tile of {pid!r}); do not rewrite {pid!r} itself")
            elif sp in walls:
                issues.append(f"the arrival spawn {sp} declared on {src} lands on a blocked tile of "
                              f"{pid!r} — fix the spawn on THAT declaration (an open tile), or open "
                              f"the tile")
            else:
                good_sources.append(sp)
        if not good_sources:
            continue  # no valid entry tile: reachability would flag every interactable, all noise

        free = {(x, y) for x in range(w) for y in range(h) if (x, y) not in walls}
        reachable = _reachable(free, good_sources)
        for xy, iid in used.items():
            if xy not in reachable:
                issues.append(f"places[{pid!r}].interactables[{iid!r}] at tile {xy} is walled off "
                              f"from the spawn — no walkable path reaches it; open the blocked tiles "
                              f"between them")
    return "; ".join(issues) if issues else None


def _return_path_error(c: Dict) -> Optional[str]:
    """Every walkable zone the player can ENTER must also be able to get BACK toward start — no
    one-way trip that strands them. A directed reachability check (`places_reachable`) is happy with
    a forward chain; this closes the loop so the map is round-trippable, the RPG norm."""
    places = c.get("places") or {}
    start = c.get("start_place")
    if not start or not isinstance(places.get(start), dict):
        return None
    fwd = views.reachable_places(list(places), places, start)
    can_return = {start}
    changed = True
    while changed:
        changed = False
        for pid, place in places.items():
            if pid in can_return or not isinstance(place, dict):
                continue
            if any(t in can_return for t in views.move_targets(place)):
                can_return.add(pid)
                changed = True
    stranded = [p for p in sorted(fwd)
                if p != start and p not in can_return
                and isinstance(places.get(p), dict) and places[p].get("kind") in _RPG_KINDS]
    if stranded:
        p = stranded[0]
        return (f"zone {p!r} can be entered but has NO way back toward the start {start!r} — it is a "
                f"one-way trip that strands the player. Add a return exit: add_interactable("
                f'place_id={p!r}, interactable={{"id":"h_back","label":"<back the way you came>",'
                f'"position":{{"cell":{{"x":..,"y":..}}}},"action":{{"type":"move","target":'
                f'"<a zone that leads back toward start>","spawn":{{"cell":{{"x":..,"y":..}}}}}}}}) '
                f"— put it on an open, reachable tile.")
    return None


def v_places(c: Dict) -> Optional[str]:
    place_ids = c.get("place_ids")
    places = c.get("places")
    if not isinstance(place_ids, list) or not place_ids:
        return "places.place_ids must be a non-empty list of place id strings"
    if not isinstance(places, dict):
        return "places.places must be an object mapping place_id -> {background, interactables}"
    missing = [p for p in place_ids if p not in places]
    if missing:
        return (f"places.place_ids lists {missing} with no entry in places.places — every "
                f"place_id needs a matching {{background, interactables}} object (or drop it "
                f"from place_ids)")
    extra = [p for p in places if p not in place_ids]
    if extra:
        return f"places.places has entries {extra} not listed in place_ids — add them to place_ids"
    if c.get("start_place") and c["start_place"] not in place_ids:
        return f"places.start_place {c['start_place']!r} is not in place_ids"
    goal = c.get("goal")
    if goal is not None and (not isinstance(goal, dict) or not goal.get("type") or not goal.get("id")):
        return "places.goal must be an object with 'type' ('flag' or 'room') and 'id'"
    for pid, place in places.items():
        if not isinstance(place, dict):
            return f"places.places[{pid!r}] must be an object"
        inter = place.get("interactables")
        if not isinstance(inter, list) or not inter:
            return f"places.places[{pid!r}].interactables must be a non-empty list"
        for j, h in enumerate(inter):
            if not isinstance(h, dict) or not h.get("id"):
                return f"places.places[{pid!r}].interactables[{j}] needs an 'id'"
            act = h.get("action")
            if not isinstance(act, dict) or not act.get("type"):
                return f"places.places[{pid!r}].interactables[{j}].action needs a 'type'"
    return _rpg_world_error(c)


SKEL_PLACES = (
    '{\n'
    '  "start_place": "room_<first>",\n'
    '  "flags": ["<flag_set_by_a_puzzle>"],\n'
    '  "goal": {"type": "flag", "id": "<flag_that_means_you_won>"},\n'
    '  "place_ids": ["room_<first>", "room_<second>"],\n'
    '  "places": {\n'
    '    "room_<first>": {\n'
    '      "kind": "room",\n'
    '      "background": "bg_<place>",\n'
    '      "interactables": [\n'
    '        {"id": "h_<thing>", "label": "<short noun>",\n'
    '         "position": {"rect": {"x": 340, "y": 210, "w": 180, "h": 160}},\n'
    '         "action": {"type": "examine",\n'
    '                    "text": "<one physical fact — material, condition, what it is for>"}}\n'
    '      ]\n'
    '    }\n'
    '  }\n'
    '}\n'
    '// A PLACE is a screen the player clicks. position.rect is pixels on a 1280x720 frame.\n'
    '// action.type is one of (you write the structured action, not Ren\'Py):\n'
    '//   examine {text}; take {item, text?}; talk {node}; move {target, requires?};\n'
    '//   use {clauses:[{requires, outcome:{text?,effects?}}], fallback?}; win {requires?}.\n'
    '//   A `use` clause\'s requires MUST be a real condition (flag/item/var) — NEVER {}. For an\n'
    '//   effect that fires UNCONDITIONALLY (e.g. a lever that always sets a flag), use a\n'
    '//   fallback ONLY and no clauses: {"type":"use","fallback":{"text":"...","effects":[...]}}.\n'
    '// background must exist in asset_manifest.backgrounds; a take/requires item id must be\n'
    '//   declared in the `items` catalogue (compose `inventory`); a talk node must exist in\n'
    '//   `nodes` (compose `scenes`); move/win targets resolve.\n'
    '// goal is OPTIONAL: if you want a win, declare a goal flag set by a reachable use-outcome\n'
    '//   and give some hotspot a win action; omit it for an open-ended world.\n'
    '// examine/use text states a PHYSICAL FACT (what it is, its condition) — never a mood word.'
)

SKEL_RPG = (
    '{\n'
    '  "start_place": "zone_<first>",\n'
    '  "flags": ["<flag_set_by_a_rune_or_fight>"],\n'
    '  "goal": {"type": "flag", "id": "<flag_that_means_you_won>"},\n'
    '  "place_ids": ["zone_<first>", "zone_<second>"],\n'
    '  "places": {\n'
    '    "zone_<first>": {\n'
    '      "kind": "world_map",\n'
    '      "layout": {\n'
    '        "size": "medium",\n'
    '        "terrain": {"open": "<walkable ground, e.g. mossy earth>",\n'
    '                    "blocked": "<impassable material, e.g. bone-pale cliff>"},\n'
    '        "features": [\n'
    '          {"id": "f_<slug>", "kind": "building", "at": "northwest",\n'
    '           "theme": "<what it looks like>", "label": "<display name>"},\n'
    '          {"id": "f_<slug2>", "kind": "clearing", "at": "center", "label": "<name>"}\n'
    '        ],\n'
    '        "exits": [{"id": "x_south", "edge": "south"}],\n'
    '        "connections": [{"from": "x_south", "to": "f_<slug2>"},\n'
    '                        {"from": "f_<slug2>", "to": "f_<slug>"}]\n'
    '      },\n'
    '      "interactables": [\n'
    '        {"id": "h_<enemy>", "label": "<short noun>",\n'
    '         "position": {"feature": "f_<slug2>"},\n'
    '         "action": {"type": "start_combat", "encounter": "enc_<slug>"}},\n'
    '        {"id": "h_<exit>", "label": "<where it leads>",\n'
    '         "position": {"feature": "x_south"},\n'
    '         "action": {"type": "move", "target": "zone_<second>",\n'
    '                    "spawn": {"feature": "x_north"}}}\n'
    '      ]\n'
    '    }\n'
    '  }\n'
    '}\n'
    '// You PLAN the map; a deterministic builder places every tile and carves the roads —\n'
    '//   connectivity is guaranteed, so think like a town plan: what exists, in which of the\n'
    '//   nine regions (northwest..center..southeast), and what connects to what.\n'
    '// feature kinds: building, fountain, camp, market_stall, rock_outcrop, tree_clump,\n'
    '//   clearing, gate. 2-5 features per zone. Every exit connects to something.\n'
    '// terrain/theme strings each become ONE generated texture: make open vs blocked CONTRAST\n'
    '//   in material, and REUSE the same strings across zones for matching terrain.\n'
    '// interactable position = {"feature": "<id>"} (its doorstep); a move\'s spawn names the\n'
    '//   arrival feature/exit in the TARGET zone. Give each zone a move BACK the way the\n'
    '//   player came — no one-way strandings.\n'
    '// Set the start ONCE with set_places_meta(start_spawn={"feature-free cell is fine"}) — or\n'
    '//   any open tile; the start zone\'s gate anchor is the natural choice.\n'
    '// TRIGGERING: walking ONTO a move/start_combat tile fires it; talk/examine/take/use/win\n'
    '//   fire on E while standing on the tile.\n'
    '// action.type: examine {text}; take {item,text?}; talk {node}; use {clauses/fallback};\n'
    '//   win {requires?}; move {target, spawn:{feature}}; start_combat {encounter}.\n'
)

_PLACE_MODE_TOOLS = frozenset({"write_component", "write_place", "edit_place", "add_interactable",
                               "read_place", "set_places_meta", "read_component", "validate",
                               "update_scratchpad", "request_review"})
_T_MIN_PLACES = frozenset({"write_component", "write_place"})
_T_INTERACT = frozenset({"read_place", "add_interactable", "edit_place"})
_T_REACH = frozenset({"read_place", "add_interactable", "edit_place", "read_component"})
_T_LAYOUT = frozenset({"read_place", "edit_place", "add_interactable", "write_place",
                       "set_places_meta", "read_component"})
# The terminal backstop covers the WHOLE IR — a dangling reference can be a talk-node (needs
# write_node/edit_node) as well as a place wiring error, so the fixer gets the broad set.
_T_TERMINAL = frozenset({"read_place", "edit_place", "add_interactable", "set_places_meta",
                         "write_place", "read_component", "write_node", "edit_node", "read_node"})
_PLACE_GUARD = {"count_tool": "write_place", "id_key": "place_id", "id_list_key": "place_ids",
                "noun": "place"}
# A `combat` game is walkable (tile map + WASD); anything else is point-and-click. The nav style
# picks the authoring skeleton + prompt (tiles XOR pixels, never mixed — no second module needed).
_COMBAT_SLICES = ("stats", "statuses", "abilities", "combatants", "encounters")


def _is_rpg(ctx) -> bool:
    return "combat" in (ctx.spec.get("modules") or [])


def _w_author_prompt(ctx) -> str:
    return "places_rpg_write.txt" if _is_rpg(ctx) else "places_write.txt"


def _w_skeleton(ctx) -> str:
    return SKEL_RPG if _is_rpg(ctx) else SKEL_PLACES


def _d_start_authored(chk, m, ctx):
    pc = ctx.artifact.get("places") or {}
    start, ids = pc.get("start_place"), pc.get("place_ids") or []
    if not start or not ids or start in ids:
        return []
    return [Error(type=chk.tier, code=chk.code, component="places", ref=start, message=(
        f"start_place {start!r} is not an authored place — the game starts nowhere, and every "
        f"other place reads as unreachable. Either call set_places_meta(start_place=<one of "
        f"{ids[:6]}>), or write_place a place with EXACTLY the id {start!r}."))]


def _d_min_places(chk, m, ctx):
    gap = ctx.param("min_places", 3) - checks.length(ctx.artifact, "places.place_ids")
    return checks.slot_errors(gap, type=chk.tier, code=chk.code, component="places",
                              noun="place") if gap > 0 else []


def _d_rpg_layout(chk, m, ctx):
    art = ctx.artifact
    errs = []
    # A walkable (combat) game must be ALL walkable — a leftover point-and-click `room` starts the
    # game as a click screen instead of WASD; rewrite it as a tile grid.
    if _is_rpg(ctx):
        places_map = (art.get("places") or {}).get("places") or {}
        room = next((pid for pid, pl in places_map.items()
                     if isinstance(pl, dict) and pl.get("kind") == "room"), None)
        if room:
            errs.append(Error(
                type=chk.tier, code=chk.code, component="places", path=room,
                message=(f"place {room!r} is a point-and-click 'room', but this is a WALKABLE game "
                         f"(the player moves an avatar with WASD). Rewrite it with write_place as "
                         f"kind 'world_map'/'town'/'interior': add a \"tiles\":{{\"legend\":..,"
                         f"\"rows\":[\"..\"]}} grid and give every interactable a "
                         f"{{\"cell\":{{\"x\":..,\"y\":..}}}} tile position (not a rect).")))
    # Walkable-map spatial integrity (grid, no overlap, not-on-wall, spawn-reachable). Returns None
    # for PnC. v_places only runs on a whole-component write; the loop authors per-place, so this is
    # what actually GATES a tile map. Surface before crossref/compile.
    layout = _rpg_world_error(art.get("places") or {})
    if layout:
        errs.append(Error(type=chk.tier, code=chk.code, component="places", message=layout))
    return errs


def _d_rpg_connectivity(chk, m, ctx):
    # No stranding: once the layout is sound, every enterable zone must get back to start. Only
    # meaningful when the maps themselves are valid.
    art = ctx.artifact
    if not _is_rpg(ctx) or _rpg_world_error(art.get("places") or {}):
        return []
    conn = _return_path_error(art.get("places") or {})
    return [Error(type=chk.tier, code=chk.code, component="places", message=conn)] if conn else []


def _d_crossref(chk, m, ctx):
    from maestro.ir_crossref import slice_token
    out = []
    for rec in checks.crossref_failures(ctx.artifact):
        # combat owns its slices' refs (it can rewrite the combat doc; world cannot).
        if slice_token(rec.get("path") or "") in _COMBAT_SLICES:
            continue
        out.append(Error(type=chk.tier, code=chk.code, component="places", message=rec["message"],
                         path=rec.get("path"), ref=rec.get("ref")))
    return out


def _d_compiles(chk, m, ctx):
    return m.wrap(chk, checks.compile_failure(ctx.run_dir, ctx.engine))


def _place_view_block(view: Dict) -> List[str]:
    edges = view.get("edges", {})
    counts = view.get("interactable_counts", {})
    unreachable = set(view.get("unreachable", []))
    place_lines = [
        f"  {pid} -> {edges.get(pid, [])}"
        f"  ({'UNREACHABLE' if pid in unreachable else 'reachable'}, "
        f"{counts.get(pid, 0)} interactables)"
        for pid in view["place_ids"]
    ]
    extra = []
    if view.get("items_never_taken"):
        extra.append(f"items never taken: {view['items_never_taken']}")
    if view.get("items_never_used"):
        extra.append(f"items never used: {view['items_never_used']}")
    return [
        "",
        "CURRENT PLACES (these already exist — reuse these EXACT ids; `move` ONLY to a place "
        "id listed here or one you also create this step):",
        *place_lines,
        *(["  " + " | ".join(extra)] if extra else []),
    ]


class World(Module):
    id = "world"
    description = ("Clickable rooms/screens you move between — a point-and-click world with "
                   "hotspots, items, and movement. Pair with `scenes` for talkable NPCs.")
    priority = 50
    component = "places"
    mode_prompt = "places_write.txt"
    mode_tools = _PLACE_MODE_TOOLS
    skeleton = SKEL_PLACES
    schemas = {"places": v_places}
    skeletons = {"places": SKEL_PLACES}
    projector = staticmethod(place_view)
    projected = True
    emits_compile = True   # a realization terminal: `places` stays writable to the end
    tool_names = ("write_place", "edit_place", "add_interactable", "read_place", "set_places_meta")

    # Every step's skeleton is style-dependent (tiles for a walkable/combat game, pixels for PnC);
    # author steps also swap the prompt. Collect the batch, then the crossref/compile terminal runs
    # `when_clean`. Adding a room is slot-guarded; everything else edits.
    checks = [
        Check("start_place", lambda chk, m, ctx: m.wrap(chk, checks.exists(
            ctx.artifact, "places.start_place")), prompt=_w_author_prompt, skeleton=_w_skeleton),
        Check("min_places", _d_min_places, prompt=_w_author_prompt, skeleton=_w_skeleton,
              tools=_T_MIN_PLACES, guard=_PLACE_GUARD),
        Check("start_authored", _d_start_authored, job="fix", prompt="places_fix.txt",
              skeleton=_w_skeleton, tools=_T_LAYOUT),
        Check("each_place_min_interactables", lambda chk, m, ctx: m.wrap(
            chk, each_place_min_interactables(ctx.artifact,
                                                     min=ctx.param("min_interactables", 2))),
              prompt=_w_author_prompt, skeleton=_w_skeleton, tools=_T_INTERACT),
        Check("places_reachable", lambda chk, m, ctx: m.wrap(chk, places_reachable(ctx.artifact)), job="fix", prompt="places_fix.txt", skeleton=_w_skeleton, tools=_T_REACH),
        Check("rpg_layout", _d_rpg_layout, job="fix", prompt="places_fix.txt", skeleton=_w_skeleton,
              tools=_T_LAYOUT),
        Check("rpg_connectivity", _d_rpg_connectivity, job="fix", prompt="places_fix.txt",
              skeleton=_w_skeleton, tools=_T_REACH),
        Check("nodes_entered", lambda chk, m, ctx: m.wrap(chk, nodes_world_entered(ctx.artifact)), job="fix", prompt="places_fix.txt", skeleton=_w_skeleton,
            tools=_T_TERMINAL),
        Check("crossref", _d_crossref, job="fix", when_clean=True, prompt="places_fix.txt",
              skeleton=_w_skeleton, tools=_T_TERMINAL),
        Check("compiles", _d_compiles, job="fix", when_clean=True, prompt="places_fix.txt",
              skeleton=_w_skeleton, tools=_T_TERMINAL),
    ]

    def params(self) -> Dict:
        return {"min_places": 3, "min_interactables": 2}

    def render_context(self, ctx: Dict) -> str:
        # The world author's context, crafted: the items to place (full catalogue — takes and
        # gates reference them), the scenes that exist (talk targets), declared encounters
        # (start_combat), locations (a room's background id), the story's shape (what winning
        # means), plus the map view. Tile rows and scene text never enter.
        from maestro.modules import assets, combat, inventory, scenes, story
        art = ctx.get("artifact") or {}
        lines = cr.spec_block(ctx) + [""] + cr.todo_block(ctx.get("todo", []))
        lines += cr.target_block(ctx)
        lines += inventory.items_block(art)
        lines += scenes.nodes_index_block(art)
        lines += combat.combat_index_block(art)
        lines += assets.locations_block(art)
        lines += story.story_block(art)
        view = ctx.get("active_view") or {}
        if view.get("place_ids"):
            lines += _place_view_block(view)
        lines += cr.story_state_block(ctx)
        lines += cr.tail_block(ctx)
        lines += ["", "Call one tool to address the first to-do item."]
        return "\n".join(lines)


MODULE = World()
register_module(MODULE)
