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
from maestro.modules.module import Error, ErrorType, Module, register_module

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
    small model can't be trusted to lay this out; the loop self-corrects off these messages."""
    places = c.get("places") or {}
    start_place = c.get("start_place")

    # Where the avatar arrives in each place: the start spawn + every move that carries a spawn.
    entries: Dict[str, list] = {}
    start_spawn = _cell_xy(c.get("start_spawn"))
    if start_place and start_spawn:
        entries.setdefault(start_place, []).append(start_spawn)
    for place in places.values():
        if not isinstance(place, dict):
            continue
        for h in place.get("interactables") or []:
            act = h.get("action") if isinstance(h, dict) else None
            if isinstance(act, dict) and act.get("type") == "move" and act.get("target"):
                sp = _cell_xy(act.get("spawn"))
                if sp:
                    entries.setdefault(act["target"], []).append(sp)

    for pid, place in places.items():
        if not isinstance(place, dict) or place.get("kind") not in _RPG_KINDS:
            continue
        w, h, walls = _tiles_dims_walls(pid, place)
        if w is None:
            return walls  # error string

        used: Dict[tuple, str] = {}
        for h_ in place.get("interactables") or []:
            iid = h_.get("id")
            xy = _cell_xy(h_.get("position"))
            if xy is None:
                return (f"places[{pid!r}].interactables[{iid!r}] needs a tile position "
                        f"'position': {{'cell': {{'x': int, 'y': int}}}} — this is a walkable map")
            if not (0 <= xy[0] < w and 0 <= xy[1] < h):
                return f"places[{pid!r}].interactables[{iid!r}] tile {xy} is outside the {w}x{h} grid"
            if xy in walls:
                return (f"places[{pid!r}].interactables[{iid!r}] sits on a blocked tile {xy} — the "
                        f"avatar can't stand there; move it onto an open tile or make that tile open")
            if xy in used:
                return (f"places[{pid!r}] puts two interactables on tile {xy} ({used[xy]} and "
                        f"{iid}) — give each its own tile")
            used[xy] = iid
            act = h_.get("action") or {}
            if act.get("type") == "move" and act.get("target") in places \
                    and places[act["target"]].get("kind") in _RPG_KINDS \
                    and _cell_xy(act.get("spawn")) is None:
                return (f"places[{pid!r}].interactables[{iid!r}] moves to walkable place "
                        f"{act['target']!r} but has no 'spawn': {{'cell': {{'x','y'}}}} — the avatar "
                        f"needs an arrival tile in the destination")

        sources = entries.get(pid, [])
        if not sources:
            if pid == start_place:
                return (f"places[{pid!r}] is the walkable start place — declare where the player "
                        f"spawns with set_places_meta(start_spawn={{'cell': {{'x': .., 'y': ..}}}})")
            continue  # an orphan (no entry): places_reachable owns that
        for sp in sources:
            if not (0 <= sp[0] < w and 0 <= sp[1] < h):
                return f"places[{pid!r}] has a start/arrival spawn {sp} outside the {w}x{h} grid"
            if sp in walls:
                return f"places[{pid!r}] has a start/arrival spawn {sp} on a blocked tile"

        free = {(x, y) for x in range(w) for y in range(h) if (x, y) not in walls}
        reachable = _reachable(free, sources)
        for xy, iid in used.items():
            if xy not in reachable:
                return (f"places[{pid!r}].interactables[{iid!r}] at tile {xy} is walled off from the "
                        f"spawn — no walkable path reaches it; open the blocked tiles between them")
    return None


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
    '         "action": {"type": "examine", "text": "It is a heavy locked door."}}\n'
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
    '//   and give some hotspot a win action; omit it for an open-ended world.'
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
    '      "tiles": {\n'
    '        "legend": {\n'
    '          ".": {"role": "open",    "theme": "<walkable ground, e.g. snow>"},\n'
    '          ",": {"role": "open",    "theme": "<a path/road>"},\n'
    '          "T": {"role": "blocked", "theme": "<an obstacle, e.g. pine>"},\n'
    '          "#": {"role": "blocked", "theme": "<a wall/cliff>"}\n'
    '        },\n'
    '        "rows": [\n'
    '          "TTTTTTTT",\n'
    '          "T......T",\n'
    '          "T.,,,..T",\n'
    '          "T.,..#.T",\n'
    '          "T.,....T",\n'
    '          "TTTTTTTT"\n'
    '        ]\n'
    '      },\n'
    '      "interactables": [\n'
    '        {"id": "h_<enemy>", "label": "<short noun>",\n'
    '         "position": {"cell": {"x": 5, "y": 3}},\n'
    '         "action": {"type": "start_combat", "encounter": "enc_<slug>"}},\n'
    '        {"id": "h_<exit>", "label": "<where it leads>",\n'
    '         "position": {"cell": {"x": 1, "y": 2}},\n'
    '         "action": {"type": "move", "target": "zone_<second>", "spawn": {"cell": {"x": 6, "y": 3}}}}\n'
    '      ]\n'
    '    }\n'
    '  }\n'
    '}\n'
    '// A WALKABLE map is PAINTED as tiles.rows — one string per grid row, each char a tile. The grid\n'
    '//   size is just the shape of rows (any size; make rows all the SAME length). The player avatar\n'
    '//   walks it with WASD. An interactable position is a {cell:{x,y}} TILE: x = column (0..width-1),\n'
    '//   y = row (0..height-1), counting from the TOP-LEFT. These are grid coords, NOT pixels.\n'
    '// tiles.legend maps each char -> {"role": "open"|"blocked", "theme": "<what it looks like>"}.\n'
    '//   role is the ONLY thing that matters to play: open = walkable, blocked = a wall the avatar\n'
    '//   cannot enter. theme is free flavour (drives the look). Paint terrain with these chars to\n'
    '//   carve real paths, rooms, water, treelines — do NOT leave an empty box. Default chars you can\n'
    '//   use without a legend entry: "." ground, "," path, "#" wall, "T" tree, "~" water, "%" rock.\n'
    '// NEVER put an interactable on a blocked tile, and NEVER wall one off — every interactable and\n'
    '//   every spawn tile must be reachable by walking from the spawn (open tiles only).\n'
    '// Set the START tile ONCE with set_places_meta(start_spawn={"cell":{"x":..,"y":..}}) — an open\n'
    '//   tile in start_place. Give EACH zone a move BACK the way the player came (a round trip), plus\n'
    '//   its forward exit — no one-way strandings.\n'
    '// TRIGGERING: walking ONTO a move/start_combat tile fires it; talk/examine/take/use/win fire\n'
    '//   when the player presses E while standing on the tile.\n'
    '// action.type: examine {text}; take {item,text?}; talk {node}; use {clauses/fallback};\n'
    '//   win {requires?}; move {target, spawn:{cell:{x,y}}}  (spawn = the arrival TILE in the\n'
    '//   destination zone, REQUIRED when moving into a walkable place); start_combat {encounter}\n'
    '//   (step onto the tile to enter that fight — compose `combat`).\n'
    '// talk node in `nodes`; item in `items`; refs resolve. (Walkable maps need no background image.)'
)

_PLACE_MODE_TOOLS = frozenset({"write_component", "write_place", "edit_place", "add_interactable",
                               "read_place", "set_places_meta", "read_component", "validate",
                               "update_scratchpad", "request_review"})
_PLACE_TARGET_JOBS = {
    "min_places": "author", "each_place_min_interactables": "author",
    "places_reachable": "fix", "rpg_layout": "fix", "rpg_connectivity": "fix",
    "crossref": "fix", "compiles": "fix",
}
_PLACE_TARGET_TOOLS = {
    "min_places": frozenset({"write_component", "write_place"}),
    "each_place_min_interactables": frozenset({"read_place", "add_interactable", "edit_place"}),
    "places_reachable": frozenset({"read_place", "add_interactable", "edit_place", "read_component"}),
    "rpg_connectivity": frozenset({"read_place", "add_interactable", "edit_place", "read_component"}),
    "rpg_layout": frozenset({"read_place", "edit_place", "add_interactable", "write_place",
                             "set_places_meta", "read_component"}),
    # crossref/compiles are the terminal backstop over the WHOLE IR — a dangling reference can be a
    # talk-node (needs write_node/edit_node) as well as a place wiring error, so the fixer gets the
    # broad set rather than routing per-slice.
    "crossref": frozenset({"read_place", "edit_place", "add_interactable", "set_places_meta",
                           "write_place", "read_component", "write_node", "edit_node", "read_node"}),
    "compiles": frozenset({"read_place", "edit_place", "add_interactable", "set_places_meta",
                           "write_place", "read_component", "write_node", "edit_node", "read_node"}),
}
_PLACE_GUARD = {"count_tool": "write_place", "id_key": "place_id", "id_list_key": "place_ids",
                "noun": "place"}


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
    prompts = {"author": "places_write.txt", "fix": "places_fix.txt"}
    target_jobs = _PLACE_TARGET_JOBS
    target_tools = _PLACE_TARGET_TOOLS
    projector = staticmethod(views.place_view)
    projected = True
    emits_compile = True   # a realization terminal: `places` stays writable to the end
    tool_names = ("write_place", "edit_place", "add_interactable", "read_place", "set_places_meta")
    create_guards = {"min_places": _PLACE_GUARD}   # adding rooms is slot-guarded; everything else edits

    @staticmethod
    def _is_rpg(context) -> bool:
        # The navigation style is already in context — the composed module set: a `combat` game is
        # walkable (tile map + WASD), anything else is point-and-click.
        return "combat" in (context.spec.get("modules") or [])

    def _apply_style(self, context) -> None:
        # Set the skeleton + author prompt the correction prompt reads, so each emitted prompt shows
        # exactly ONE mental model — tiles XOR pixels, never mixed. No second module needed.
        rpg = self._is_rpg(context)
        self.skeleton = SKEL_RPG if rpg else SKEL_PLACES
        self.skeletons = {"places": self.skeleton}
        self.prompts = {"author": "places_rpg_write.txt" if rpg else "places_write.txt",
                        "fix": "places_fix.txt"}

    def get_correction_prompt(self, context, error: Error):
        self._apply_style(context)
        return super().get_correction_prompt(context, error)

    def params(self) -> Dict:
        return {"min_places": 3, "min_interactables": 2}

    def _add(self, errs, result, code):
        tier = ErrorType.BUILD if self.job_for(code) == "author" else ErrorType.FIX
        e = checks.as_error(result, type=tier, code=code, component="places")
        if e:
            errs.append(e)

    def get_errors(self, context) -> List[Error]:
        art = context.artifact
        errs: List[Error] = []
        self._add(errs, checks.exists(art, "places.start_place"), "start_place")
        gap = context.param("min_places", 3) - checks.length(art, "places.place_ids")
        if gap > 0:
            errs += checks.slot_errors(gap, type=ErrorType.BUILD, code="min_places",
                                       component="places", noun="place")
        self._add(errs, checks.each_place_min_interactables(
            art, min=context.param("min_interactables", 2)), "each_place_min_interactables")
        self._add(errs, checks.places_reachable(art), "places_reachable")
        # A walkable (combat) game must be ALL walkable — no point-and-click `room` zones mixed in,
        # or the game starts as a click screen instead of WASD. Enforce the kind the RPG skeleton asks
        # for; the fix rewrites that place with a grid + cell positions.
        if self._is_rpg(context):
            places_map = (art.get("places") or {}).get("places") or {}
            room = next((pid for pid, pl in places_map.items()
                         if isinstance(pl, dict) and pl.get("kind") == "room"), None)
            if room:
                errs.append(Error(
                    type=ErrorType.FIX, code="rpg_layout", component="places", path=room,
                    message=(f"place {room!r} is a point-and-click 'room', but this is a WALKABLE game "
                             f"(the player moves an avatar with WASD). Rewrite it with write_place as "
                             f"kind 'world_map'/'town'/'interior': add a \"tiles\":{{\"legend\":..,"
                             f"\"rows\":[\"..\"]}} grid and give every interactable a "
                             f"{{\"cell\":{{\"x\":..,\"y\":..}}}} tile position (not a rect).")))
        # Walkable-map layout (tile grid, no overlap, not-on-wall, spawn-reachable). Returns None
        # for PnC. v_places only runs on a whole-component write; the loop authors per-place, so this
        # is what actually GATES a tile map's spatial integrity. Surface before crossref/compile.
        layout = _rpg_world_error(art.get("places") or {})
        if layout:
            errs.append(Error(type=ErrorType.FIX, code="rpg_layout", component="places",
                              message=layout))
        # No stranding: once the layout is sound, every enterable zone must be able to get back to
        # start (bidirectional move graph). Only meaningful when the maps themselves are valid.
        elif self._is_rpg(context):
            conn = _return_path_error(art.get("places") or {})
            if conn:
                errs.append(Error(type=ErrorType.FIX, code="rpg_connectivity", component="places",
                                  message=conn))
        if not errs:
            from maestro.ir_crossref import slice_token
            for rec in checks.crossref_failures(art):
                # combat owns its slices' refs (it can rewrite the combat doc; world cannot).
                if slice_token(rec.get("path", "")) in ("stats", "statuses", "abilities",
                                                         "combatants", "encounters"):
                    continue
                errs.append(Error(type=ErrorType.FIX, code="crossref", component="places",
                                  message=rec["message"], path=rec.get("path"), ref=rec.get("ref")))
            if not errs:
                ce = checks.as_error(checks.compile_failure(context.run_dir, context.engine),
                                     type=ErrorType.FIX, code="compiles", component="places")
                if ce:
                    errs.append(ce)
        return errs

    def job_for(self, code: str) -> str:
        if code == "start_place":
            return "author"
        return super().job_for(code)

    def render_context(self, ctx: Dict) -> str:
        lines = cr.spec_block(ctx) + [""] + cr.todo_block(ctx.get("todo", []))
        lines += cr.target_block(ctx)
        lines += cr.scratchpad_block(ctx)
        lines += cr.upstream_block(ctx.get("upstream") or {})
        view = ctx.get("active_view") or {}
        if view.get("place_ids"):
            lines += _place_view_block(view)
        lines += cr.story_state_block(ctx)
        lines += cr.tail_block(ctx)
        lines += ["", "Call one tool to address the first to-do item."]
        return "\n".join(lines)


MODULE = World()
register_module(MODULE)
