"""Fine-resolution town maps — the tier between the macro world and interiors.

A settlement on the macro grid is ~6 cells: far too coarse to read as a town. So each
settlement gets its own walkable town zone generated at building resolution: gates on the
edges the world roads actually leave toward, streets running gate -> central plaza, and
buildings on parcels whose doorstep sits beside a street (every building faces the road by
construction). One named building per interior hosted at the settlement — its doorstep is
the enter-hotspot cell — plus ambient houses to density. Deterministic per
(world seed, settlement id).
"""

import random
from collections import deque

_NEIGHBORS = ((1, 0), (-1, 0), (0, 1), (0, -1))

_ROAD_THEME = "worn path"
_PLAZA_THEME = "cobbled plaza"
_BUILDING_THEME = "timber and plaster wall"
_SEAL_THEME = "dense hedgerow"
_WELL_THEME = "stone well"
_HOUSE_LABELS = ("timber house", "cottage", "storehouse", "market stall", "workshop",
                 "granary")

_MIN_BUILDINGS = 10
_MAX_BUILDINGS = 16
_BUILDING_DIMS = ((6, 4), (5, 4), (5, 5), (4, 4), (4, 3), (3, 3), (3, 2))
_MIN_LANES, _MAX_LANES = 2, 4
_LANE_MIN, _LANE_MAX = 5, 9
_CHAR_POOL = list("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789")

_EDGES = ("n", "e", "s", "w")


def build_town(site: dict, hosted_ids: list, world: dict, seed_base) -> dict:
    rng = random.Random(f"{seed_base}:town:{site['id']}")
    n_buildings = min(_MAX_BUILDINGS, max(_MIN_BUILDINGS, len(hosted_ids) + 6))
    w = 34 + 2 * max(0, n_buildings - _MIN_BUILDINGS)
    h = 24 + max(0, n_buildings - _MIN_BUILDINGS)

    ground = _ground_theme(site, world)
    theme = [[ground] * w for _ in range(h)]
    role = [["open"] * w for _ in range(h)]

    plaza = (w // 2, h // 2)
    plaza_cells = {(plaza[0] + dx, plaza[1] + dy) for dy in (-1, 0, 1) for dx in (-1, 0, 1)}
    for x, y in plaza_cells:
        theme[y][x] = _PLAZA_THEME

    road_cells = set()
    gates = []
    for edge in _gate_edges(site, world):
        gate = _gate_cell(edge, w, h, rng)
        path = _carve_road(edge, gate, plaza, plaza_cells)
        for x, y in path:
            if (x, y) not in plaza_cells:
                theme[y][x] = _ROAD_THEME
            road_cells.add((x, y))
        gates.append({"cell": gate, "inward": path[min(2, len(path) - 1)], "edge": edge})

    for x, y in _carve_lanes(road_cells, plaza_cells, {g["cell"] for g in gates}, w, h, rng):
        theme[y][x] = _ROAD_THEME
        road_cells.add((x, y))

    reserved = set(road_cells) | plaza_cells

    well = plaza
    theme[well[1]][well[0]] = _WELL_THEME
    role[well[1]][well[0]] = "blocked"
    plaza = (plaza[0], plaza[1] + 1)

    footprints = {"well": {"x": well[0], "y": well[1], "w": 1, "h": 1,
                           "label": _WELL_THEME}}
    doors = {}
    hotspot_cells = {g["cell"] for g in gates}
    jobs = [("bld_" + i, _humanize(i), i) for i in hosted_ids]
    jobs += [(f"house_{n}", rng.choice(_HOUSE_LABELS), None)
             for n in range(n_buildings - len(hosted_ids))]
    for fid, label, interior_id in jobs:
        placed = _place_building(theme, role, reserved, road_cells, hotspot_cells, w, h, rng)
        if placed is None:
            continue
        rect, door = placed
        footprints[fid] = {"x": rect[0], "y": rect[1], "w": rect[2], "h": rect[3],
                           "label": label, "door": [door[0], door[1]]}
        if interior_id is not None:
            doors[interior_id] = door
            hotspot_cells.add(door)

    _seal_pockets(theme, role, plaza, w, h)

    rows, legend = _paint(theme, role, w, h)
    role_grid = [[legend[ch]["role"] for ch in row] for row in rows]
    return {
        "tiles": {"rows": rows, "legend": legend},
        "footprints": footprints,
        "doors": doors,
        "gates": gates,
        "plaza": plaza,
        "role_grid": role_grid,
        "ww": w,
        "wh": h,
    }


def _humanize(interior_id: str) -> str:
    return interior_id.replace("_", " ").strip() or "town building"


def _ground_theme(site, world):
    biome, water = world["biome"], world["water"]
    counts = {}
    cells = site.get("cells") or [[site["x"], site["y"]]]
    for cx, cy in cells:
        if not water[cy][cx]:
            t = biome[cy][cx]
            counts[t] = counts.get(t, 0) + 1
    if not counts:
        return biome[site["y"]][site["x"]]
    return max(sorted(counts), key=lambda t: counts[t])


def _gate_edges(site, world):
    """Edges the world roads actually leave this settlement toward — so walking out of the
    north gate matches heading north on the macro map. Falls back to a single south gate for
    a roadless (single-settlement) world."""
    footprint = {tuple(c) for c in (site.get("cells") or [[site["x"], site["y"]]])}
    sx, sy = site["x"], site["y"]
    edges = []
    for path in world["roads"]:
        touches = any(tuple(c) in footprint for c in path)
        if not touches:
            continue
        for cx, cy in path:
            if (cx, cy) in footprint:
                continue
            if any((cx + dx, cy + dy) in footprint for dx, dy in _NEIGHBORS):
                dx, dy = cx - sx, cy - sy
                if abs(dx) >= abs(dy):
                    edge = "e" if dx > 0 else "w"
                else:
                    edge = "s" if dy > 0 else "n"
                if edge not in edges:
                    edges.append(edge)
                break
    return edges or ["s"]


def _gate_cell(edge, w, h, rng):
    if edge == "n":
        return (rng.randrange(w // 3, 2 * w // 3), 0)
    if edge == "s":
        return (rng.randrange(w // 3, 2 * w // 3), h - 1)
    if edge == "w":
        return (0, rng.randrange(h // 3, 2 * h // 3))
    return (w - 1, rng.randrange(h // 3, 2 * h // 3))


def _carve_lanes(road_cells, plaza_cells, gate_cells, w, h, rng):
    """Dead-end side lanes off the existing streets — extra frontage so buildings don't all
    crowd the gate roads. A lane starts at a random street cell, runs perpendicular-ish away
    from it, and stops short of the map edge."""
    lanes = set()
    candidates = sorted(road_cells - plaza_cells - gate_cells)
    if not candidates:
        return lanes
    for _ in range(rng.randint(_MIN_LANES, _MAX_LANES)):
        sx, sy = candidates[rng.randrange(len(candidates))]
        dx, dy = rng.choice(_NEIGHBORS)
        length = rng.randint(_LANE_MIN, _LANE_MAX)
        x, y = sx, sy
        for _ in range(length):
            x, y = x + dx, y + dy
            if not (2 <= x < w - 2 and 2 <= y < h - 2):
                break
            if (x, y) in plaza_cells or (x, y) in gate_cells:
                break
            lanes.add((x, y))
    return lanes


def _carve_road(edge, gate, plaza, plaza_cells):
    """L-path: walk the entry axis first (a west/east gate moves in x, a north/south gate in
    y), then turn toward the plaza. Ends on the first plaza cell touched."""
    px, py = plaza
    x, y = gate
    path = [(x, y)]
    walk_x_first = edge in ("e", "w")
    while (x, y) not in plaza_cells:
        if walk_x_first and x != px:
            x += 1 if px > x else -1
        elif y != py:
            y += 1 if py > y else -1
        else:
            x += 1 if px > x else -1
        path.append((x, y))
    return path


def _place_building(theme, role, reserved, road_cells, hotspot_cells, w, h, rng):
    """Street-facing parcel: doorstep beside a road cell, building rect behind the doorstep.
    A doorstep never lands on or 4-adjacent to another hotspot cell (a gate or an earlier
    door) — doorsteps are enter-hotspots, and portals side by side is the exact bug the town
    tier exists to kill. Falls back to any spot with an open neighbour when no street
    frontage fits."""
    fronts = list(road_cells)
    rng.shuffle(fronts)
    dirs = list(_NEIGHBORS)
    for bw, bh in rng.sample(_BUILDING_DIMS, len(_BUILDING_DIMS)):
        for rx, ry in fronts:
            rng.shuffle(dirs)
            for dx, dy in dirs:
                door = (rx + dx, ry + dy)
                rect = _rect_behind(door, (dx, dy), bw, bh)
                if _fits(rect, door, role, reserved, hotspot_cells, w, h):
                    return _claim(rect, door, theme, role, reserved)
    for bw, bh in _BUILDING_DIMS:
        for _ in range(200):
            x0 = rng.randrange(1, max(2, w - bw - 1))
            y0 = rng.randrange(1, max(2, h - bh - 1))
            rect = (x0, y0, bw, bh)
            door = (x0 + bw // 2, y0 + bh)
            if _fits(rect, door, role, reserved, hotspot_cells, w, h):
                return _claim(rect, door, theme, role, reserved)
    return None


def _rect_behind(door, direction, bw, bh):
    dx, dy = direction
    x, y = door
    if dy == 1:
        return (x - bw // 2, y + 1, bw, bh)
    if dy == -1:
        return (x - bw // 2, y - bh, bw, bh)
    if dx == 1:
        return (x + 1, y - bh // 2, bw, bh)
    return (x - bw, y - bh // 2, bw, bh)


def _fits(rect, door, role, reserved, hotspot_cells, w, h):
    x0, y0, bw, bh = rect
    if not (1 <= x0 and x0 + bw <= w - 1 and 1 <= y0 and y0 + bh <= h - 1):
        return False
    if not (0 <= door[0] < w and 0 <= door[1] < h):
        return False
    if door in reserved or role[door[1]][door[0]] != "open":
        return False
    if door in hotspot_cells or any((door[0] + dx, door[1] + dy) in hotspot_cells
                                    for dx, dy in _NEIGHBORS):
        return False
    for y in range(y0, y0 + bh):
        for x in range(x0, x0 + bw):
            if (x, y) in reserved or role[y][x] != "open":
                return False
    return True


def _claim(rect, door, theme, role, reserved):
    x0, y0, bw, bh = rect
    for y in range(y0, y0 + bh):
        for x in range(x0, x0 + bw):
            role[y][x] = "blocked"
            theme[y][x] = _BUILDING_THEME
            reserved.add((x, y))
    reserved.add(door)
    return rect, door


def _seal_pockets(theme, role, plaza, w, h):
    """Open cells a player can never reach (walled off behind buildings) become blocked
    hedgerow, so nothing walkable sits outside the plaza's component."""
    seen = set()
    dq = deque([plaza])
    seen.add(plaza)
    while dq:
        x, y = dq.popleft()
        for dx, dy in _NEIGHBORS:
            nx, ny = x + dx, y + dy
            if (0 <= nx < w and 0 <= ny < h and (nx, ny) not in seen
                    and role[ny][nx] == "open"):
                seen.add((nx, ny))
                dq.append((nx, ny))
    for y in range(h):
        for x in range(w):
            if role[y][x] == "open" and (x, y) not in seen:
                role[y][x] = "blocked"
                theme[y][x] = _SEAL_THEME


def _paint(theme_grid, role_grid, w, h):
    combo = {}
    rows = []
    for y in range(h):
        chars = []
        for x in range(w):
            key = (theme_grid[y][x], role_grid[y][x])
            ch = combo.get(key)
            if ch is None:
                ch = _CHAR_POOL[len(combo)]
                combo[key] = ch
            chars.append(ch)
        rows.append("".join(chars))
    legend = {ch: {"role": role, "theme": theme} for (theme, role), ch in combo.items()}
    return rows, legend
