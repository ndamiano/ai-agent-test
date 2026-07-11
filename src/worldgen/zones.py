"""Carve the macro world into playable `places` zones (see docs/game_ir.schema.json).

One walkable map per real discontinuity: a landmass with at least one located site becomes ONE
place spanning its whole bounding box (padded, capped, re-detailed with WFC for local theme
variety), so a continuous island is walked, never diced into arbitrary per-site crops. Sites on
that landmass are in-map examine markers, not exits — two beaches on the same island are a walk,
not a load screen. A single ocean place (the whole world, water/land inverted) is the only thing
that crosses a real discontinuity (open water between landmasses); it exists only when the graph
actually needs it (an authored open_water site, or more than one landmass zone). Role (open/
blocked) always comes from the macro water mask, never from WFC — WFC only ever picks cosmetic
themes, so a contradiction (falling back to the raw crop) can never break walkability. Interior
sites get a small fixed room instead of a world sample. Connectivity (`move` interactables) is
derived from geometry, never authored.
"""

from collections import deque

from . import wfc

_MAX_ZONE_W, _MAX_ZONE_H = 64, 48
_ZONE_PAD = 2
_ROAD_THEME = "worn path"
_CHAR_POOL = list("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789")
_IROOM_W, _IROOM_H = 12, 9
_IROOM_LEGEND = {"#": {"role": "blocked", "theme": "stone wall"},
                 ".": {"role": "open", "theme": "wooden floor"}}
_NEIGHBORS = ((1, 0), (-1, 0), (0, 1), (0, -1))


def build_zones(world: dict, recipe: dict) -> dict:
    sites = world["sites"]
    located = [s for s in sites if not s.get("host")]
    interiors = [s for s in sites if s.get("host")]
    seed_base = (world.get("meta") or {}).get("seed", 0)
    W, H = world["size"]["w"], world["size"]["h"]
    water = world["water"]

    all_site_cells = _collect_site_cells(located)
    all_road_cells = {tuple(c) for path in world["roads"] for c in path}

    comp = _components(world)
    bboxes = _all_bboxes(comp, W, H)

    land_sites = [s for s in located if not water[s["y"]][s["x"]]]
    water_sites = [s for s in located if water[s["y"]][s["x"]]]

    landmass_groups = {}
    for s in land_sites:
        landmass_groups.setdefault(comp[s["y"]][s["x"]], []).append(s)

    places = {}
    zone_meta = {}
    site_place_id = {}

    for cid, group in landmass_groups.items():
        primary = next((s for s in group if s["type"] == "settlement"), group[0])
        pid = primary["id"]
        kind = "town" if any(s["type"] == "settlement" for s in group) else "world_map"

        x0, x1, y0, y1 = bboxes[cid]
        wx0, wy0, ww0, wh0 = _pad_bbox((x0, x1, y0, y1), W, H, _ZONE_PAD)
        scale = 2 if (ww0 > _MAX_ZONE_W or wh0 > _MAX_ZONE_H) else 1

        rows, legend, role_grid, elev_rows, ww, wh = _sample_window(
            world, wx0, wy0, ww0, wh0, scale, pid, seed_base, all_site_cells, all_road_cells,
            invert=False)

        meta = {"wx0": wx0, "wy0": wy0, "scale": scale, "ww": ww, "wh": wh,
                "role_grid": role_grid, "used": set(), "site_local": {}, "primary": primary}
        zone_meta[pid] = meta

        interactables = []
        for s in group:
            local = _to_local(meta, s["x"], s["y"])
            meta["site_local"][s["id"]] = local
            reach = _reach_from(meta, local)
            cell = _reserve_near(local, reach, meta["used"], ww, wh)
            interactables.append(_examine_hotspot(s, cell))
            site_place_id[s["id"]] = pid

        places[pid] = {
            "kind": kind,
            "tiles": {"rows": rows, "legend": legend},
            "interactables": interactables,
            "layout": {"window": [wx0, wy0, ww0, wh0], "elevation": elev_rows},
        }

    open_water_sites = [s for s in water_sites if s["type"] == "open_water"]
    need_ocean = bool(water_sites) or len(landmass_groups) > 1
    if need_ocean:
        ocean_id = open_water_sites[0]["id"] if open_water_sites else (
            water_sites[0]["id"] if water_sites else "open_sea")
        scale_o = 2 if W > _MAX_ZONE_W else 1

        rows_o, legend_o, role_o, elev_o, oww, owh = _sample_window(
            world, 0, 0, W, H, scale_o, ocean_id, seed_base, all_site_cells, all_road_cells,
            invert=True)

        ometa = {"wx0": 0, "wy0": 0, "scale": scale_o, "ww": oww, "wh": owh,
                 "role_grid": role_o, "used": set(), "site_local": {}}
        zone_meta[ocean_id] = ometa

        interactables_o = []
        for s in water_sites:
            local = _to_local(ometa, s["x"], s["y"])
            ometa["site_local"][s["id"]] = local
            reach = _reach_from(ometa, local)
            cell = _reserve_near(local, reach, ometa["used"], oww, owh)
            interactables_o.append(_examine_hotspot(s, cell))
            site_place_id[s["id"]] = ocean_id

        places[ocean_id] = {
            "kind": "world_map",
            "tiles": {"rows": rows_o, "legend": legend_o},
            "interactables": interactables_o,
            "layout": {"window": [0, 0, W, H], "elevation": elev_o},
        }

        for cid, group in landmass_groups.items():
            island_id = next((s for s in group if s["type"] == "settlement"), group[0])["id"]
            _wire_sea_link(world, places, zone_meta, island_id, ocean_id)

    for site in interiors:
        rows, legend = _interior_room()
        role_grid = [[legend[ch]["role"] for ch in row] for row in rows]
        center = (_IROOM_W // 2, _IROOM_H // 2)
        door = (_IROOM_W // 2, _IROOM_H - 2)
        places[site["id"]] = {
            "kind": "interior",
            "tiles": {"rows": rows, "legend": legend},
            "interactables": [_examine_hotspot(site, center)],
        }
        zone_meta[site["id"]] = {"ww": _IROOM_W, "wh": _IROOM_H, "role_grid": role_grid,
                                  "used": {center}, "door": door}

    for site in interiors:
        host_place = site_place_id[site["host"]]
        _wire_interior(places, zone_meta, host_place, site["host"], site["id"])

    settlement = next(s for s in sites if s["type"] == "settlement")
    start_place = site_place_id[settlement["id"]]
    smeta = zone_meta[start_place]
    anchor = smeta["site_local"][settlement["id"]]
    reach = _reach_from(smeta, anchor)
    start_spawn = _reserve_near(anchor, reach, {anchor}, smeta["ww"], smeta["wh"])

    return {
        "place_ids": list(places.keys()),
        "places": places,
        "start_place": start_place,
        "start_spawn": {"cell": {"x": start_spawn[0], "y": start_spawn[1]}},
    }


def render_zone(place: dict) -> str:
    tiles = place.get("tiles") or {}
    rows = tiles.get("rows", [])
    legend = tiles.get("legend", {})
    overlay = {}
    for h in place.get("interactables", []):
        cell = (h.get("position") or {}).get("cell")
        if cell:
            overlay[(cell["x"], cell["y"])] = (h.get("id") or "?")[2].upper()

    w = len(rows[0]) if rows else 0
    out = [f"{place.get('kind')} {w}x{len(rows)}", ""]
    for y, row in enumerate(rows):
        out.append("".join(overlay.get((x, y), ch) for x, ch in enumerate(row)))
    out += ["", "Legend:"]
    for ch, spec in sorted(legend.items()):
        out.append(f"  {ch} = {spec.get('theme')} ({spec.get('role')})")
    out += ["", "Interactables:"]
    for h in place.get("interactables", []):
        cell = (h.get("position") or {}).get("cell")
        act = h.get("action") or {}
        target = f" -> {act.get('target')}" if act.get("target") else ""
        out.append(f"  {h.get('id')} @ {cell} [{act.get('type')}]{target}")
    return "\n".join(out)


# ── landmass grouping: connected components over the macro water mask ─────────
def _components(world):
    W, H = world["size"]["w"], world["size"]["h"]
    water = world["water"]
    comp = [[-1] * W for _ in range(H)]
    cid = 0
    for sy in range(H):
        for sx in range(W):
            if comp[sy][sx] != -1:
                continue
            kind = water[sy][sx]
            dq = deque([(sx, sy)])
            comp[sy][sx] = cid
            while dq:
                x, y = dq.popleft()
                for dx, dy in _NEIGHBORS:
                    nx, ny = x + dx, y + dy
                    if 0 <= nx < W and 0 <= ny < H and comp[ny][nx] == -1 and water[ny][nx] == kind:
                        comp[ny][nx] = cid
                        dq.append((nx, ny))
            cid += 1
    return comp


def _all_bboxes(comp, W, H):
    bounds = {}
    for y in range(H):
        for x in range(W):
            cid = comp[y][x]
            b = bounds.get(cid)
            if b is None:
                bounds[cid] = [x, x, y, y]
            else:
                if x < b[0]: b[0] = x
                if x > b[1]: b[1] = x
                if y < b[2]: b[2] = y
                if y > b[3]: b[3] = y
    return {cid: tuple(b) for cid, b in bounds.items()}


def _pad_bbox(bounds, W, H, pad):
    minx, maxx, miny, maxy = bounds
    x0 = max(0, minx - pad)
    y0 = max(0, miny - pad)
    x1 = min(W - 1, maxx + pad)
    y1 = min(H - 1, maxy + pad)
    return x0, y0, x1 - x0 + 1, y1 - y0 + 1


# ── zone sampling (macro crop + optional 2x downsample + WFC re-detail) ───────
def _downsample_blocks(w, h):
    nw, nh = (w + 1) // 2, (h + 1) // 2
    for oy in range(nh):
        for ox in range(nw):
            cells = [(ox * 2 + dx, oy * 2 + dy) for dy in (0, 1) for dx in (0, 1)
                     if ox * 2 + dx < w and oy * 2 + dy < h]
            yield oy, ox, cells


def _downsample_majority(grid, w, h):
    nw, nh = (w + 1) // 2, (h + 1) // 2
    out = [[False] * nw for _ in range(nh)]
    for oy, ox, cells in _downsample_blocks(w, h):
        vals = [grid[y][x] for x, y in cells]
        out[oy][ox] = sum(1 for v in vals if v) * 2 > len(vals)
    return out


def _downsample_any(grid, w, h):
    nw, nh = (w + 1) // 2, (h + 1) // 2
    out = [[False] * nw for _ in range(nh)]
    for oy, ox, cells in _downsample_blocks(w, h):
        out[oy][ox] = any(grid[y][x] for x, y in cells)
    return out


def _downsample_categorical_matching(grid, water_grid, water_out, w, h):
    """Categorical majority vote, restricted to the sub-cells whose own water-ness matches the
    block's downsampled water-ness. Ties in `_downsample_majority` are already water/land
    votes; picking a theme from an off-side sub-cell would let a water biome (e.g. "sea") land
    on a block classified open/land (or vice versa) — the exact split `_sample_window` relies on
    to keep a theme from ever spanning both roles."""
    nw, nh = (w + 1) // 2, (h + 1) // 2
    out = [[None] * nw for _ in range(nh)]
    for oy, ox, cells in _downsample_blocks(w, h):
        want_water = water_out[oy][ox]
        vals = [grid[y][x] for x, y in cells if water_grid[y][x] == want_water]
        if not vals:
            vals = [grid[y][x] for x, y in cells]
        counts = {}
        for v in vals:
            counts[v] = counts.get(v, 0) + 1
        out[oy][ox] = max(vals, key=lambda v: (counts[v], -vals.index(v)))
    return out


def _downsample_average(grid, w, h):
    nw, nh = (w + 1) // 2, (h + 1) // 2
    out = [[0.0] * nw for _ in range(nh)]
    for oy, ox, cells in _downsample_blocks(w, h):
        vals = [grid[y][x] for x, y in cells]
        out[oy][ox] = sum(vals) / len(vals)
    return out


def _sample_window(world, wx0, wy0, ww0, wh0, scale, seed_key, seed_base, all_site_cells,
                    all_road_cells, invert):
    """Crop [wx0, wy0, ww0, wh0] of the world, optionally halved (majority-vote per 2x2 block),
    and re-detail its themes with WFC. `invert` flips role open<->blocked for the ocean map
    (water walkable, land blocked) vs a landmass map (land walkable, water blocked)."""
    biome, water, elevation = world["biome"], world["water"], world["elevation"]

    full_water = [[bool(water[wy0 + y][wx0 + x]) for x in range(ww0)] for y in range(wh0)]
    full_theme = [[biome[wy0 + y][wx0 + x] for x in range(ww0)] for y in range(wh0)]
    full_elev = [[float(elevation[wy0 + y][wx0 + x]) for x in range(ww0)] for y in range(wh0)]
    full_road = [[(wx0 + x, wy0 + y) in all_road_cells for x in range(ww0)] for y in range(wh0)]

    if scale == 2:
        water_g = _downsample_majority(full_water, ww0, wh0)
        theme_g = _downsample_categorical_matching(full_theme, full_water, water_g, ww0, wh0)
        elev_g = _downsample_average(full_elev, ww0, wh0)
        road_g = _downsample_any(full_road, ww0, wh0)
    else:
        water_g, theme_g, elev_g, road_g = full_water, full_theme, full_elev, full_road
    ww, wh = len(water_g[0]), len(water_g)

    if not invert:
        for y in range(wh):
            for x in range(ww):
                if road_g[y][x]:
                    theme_g[y][x] = _ROAD_THEME

    role_grid = [[None] * ww for _ in range(wh)]
    for y in range(wh):
        for x in range(ww):
            if road_g[y][x] and not invert:
                role_grid[y][x] = "open"
            else:
                open_role = water_g[y][x] if invert else (not water_g[y][x])
                role_grid[y][x] = "open" if open_role else "blocked"

    site_cells_local = set()
    for gx, gy in all_site_cells:
        if wx0 <= gx < wx0 + ww0 and wy0 <= gy < wy0 + wh0:
            site_cells_local.add(((gx - wx0) // scale, (gy - wy0) // scale))

    pins = {}
    for y in range(wh):
        for x in range(ww):
            is_road = road_g[y][x] and not invert
            is_boundary = any(0 <= x + dx < ww and 0 <= y + dy < wh
                               and water_g[y + dy][x + dx] != water_g[y][x]
                               for dx, dy in _NEIGHBORS)
            if is_road or is_boundary or (x, y) in site_cells_local:
                pins[(x, y)] = theme_g[y][x]

    tiles = sorted({t for row in theme_g for t in row})
    if _ROAD_THEME not in tiles:
        tiles.append(_ROAD_THEME)

    # A theme never crosses the land/water line: same texture walkable AND blocked
    # would render as invisible walls.
    water_themes = {theme_g[y][x] for y in range(wh) for x in range(ww) if water_g[y][x]}
    land_themes = set(tiles) - water_themes
    domains = {}
    for y in range(wh):
        for x in range(ww):
            allowed = water_themes if water_g[y][x] else land_themes
            if allowed:
                domains[(x, y)] = allowed

    adjacency_set = set()
    for y in range(wh):
        for x in range(ww):
            t = theme_g[y][x]
            if x + 1 < ww and theme_g[y][x + 1] != t:
                adjacency_set.add(tuple(sorted((t, theme_g[y][x + 1]))))
            if y + 1 < wh and theme_g[y + 1][x] != t:
                adjacency_set.add(tuple(sorted((t, theme_g[y + 1][x]))))

    counts = {}
    for row in theme_g:
        for t in row:
            counts[t] = counts.get(t, 0) + 1
    weights = {t: float(counts.get(t, 1)) for t in tiles}

    try:
        detailed = wfc.collapse(ww, wh, tiles, [list(p) for p in adjacency_set],
                                 weights=weights, pins=pins, domains=domains,
                                 seed=f"{seed_base}:{seed_key}", max_restarts=20)
    except wfc.ContradictionError:
        detailed = theme_g

    rows, legend = _paint(detailed, role_grid, ww, wh)
    return rows, legend, role_grid, elev_g, ww, wh


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


def _interior_room():
    rows = []
    for y in range(_IROOM_H):
        row = "".join("#" if x in (0, _IROOM_W - 1) or y in (0, _IROOM_H - 1) else "."
                       for x in range(_IROOM_W))
        rows.append(row)
    return rows, dict(_IROOM_LEGEND)


def _collect_site_cells(located):
    out = set()
    for s in located:
        cells = s.get("cells") or ([(s["x"], s["y"])] if s.get("x") is not None else [])
        out.update(tuple(c) for c in cells)
    return out


# ── window <-> local coordinate mapping ─────────────────────────────────────────
def _to_local(meta, gx, gy):
    lx = (gx - meta["wx0"]) // meta["scale"]
    ly = (gy - meta["wy0"]) // meta["scale"]
    lx = min(max(lx, 0), meta["ww"] - 1)
    ly = min(max(ly, 0), meta["wh"] - 1)
    return (lx, ly)


def _to_global(meta, cell):
    lx, ly = cell
    return (meta["wx0"] + lx * meta["scale"], meta["wy0"] + ly * meta["scale"])


# ── flood fill + cell reservation (placement is confined to one connected blob,
# so anything placed is provably reachable from its own arrival point) ─────────
def _flood(role_grid, w, h, start):
    sx, sy = start
    if not (0 <= sx < w and 0 <= sy < h) or role_grid[sy][sx] != "open":
        return set()
    seen = {(sx, sy)}
    dq = deque(seen)
    while dq:
        x, y = dq.popleft()
        for dx, dy in _NEIGHBORS:
            nx, ny = x + dx, y + dy
            if 0 <= nx < w and 0 <= ny < h and (nx, ny) not in seen and role_grid[ny][nx] == "open":
                seen.add((nx, ny))
                dq.append((nx, ny))
    return seen


def _nearest_open(role_grid, w, h, target):
    tx = min(max(target[0], 0), w - 1)
    ty = min(max(target[1], 0), h - 1)
    if role_grid[ty][tx] == "open":
        return (tx, ty)
    seen = {(tx, ty)}
    dq = deque(seen)
    while dq:
        x, y = dq.popleft()
        for dx, dy in _NEIGHBORS:
            nx, ny = x + dx, y + dy
            if 0 <= nx < w and 0 <= ny < h and (nx, ny) not in seen:
                if role_grid[ny][nx] == "open":
                    return (nx, ny)
                seen.add((nx, ny))
                dq.append((nx, ny))
    raise ValueError("no open cell found in grid")


def _reach_from(meta, target):
    anchor = _nearest_open(meta["role_grid"], meta["ww"], meta["wh"], target)
    return _flood(meta["role_grid"], meta["ww"], meta["wh"], anchor)


def _reserve_near(target, allowed, used, w, h):
    tx, ty = target
    if 0 <= tx < w and 0 <= ty < h and (tx, ty) in allowed and (tx, ty) not in used:
        used.add((tx, ty))
        return (tx, ty)
    seen = {(tx, ty)}
    dq = deque(seen)
    while dq:
        x, y = dq.popleft()
        for dx, dy in _NEIGHBORS:
            nx, ny = x + dx, y + dy
            if 0 <= nx < w and 0 <= ny < h and (nx, ny) not in seen:
                seen.add((nx, ny))
                if (nx, ny) in allowed and (nx, ny) not in used:
                    used.add((nx, ny))
                    return (nx, ny)
                dq.append((nx, ny))
    raise ValueError("no free reachable cell to place an interactable")


def _examine_hotspot(site, cell):
    return {
        "id": f"h_{site['id']}_marker",
        "label": site["id"],
        "position": {"cell": {"x": cell[0], "y": cell[1]}},
        "action": {"type": "examine", "text": f"{site['id'].replace('_', ' ')} ({site['type']})."},
    }


# ── real discontinuities: island <-> ocean, host <-> interior ─────────────────
def _distance_to_blocked(role_grid, w, h):
    dist = [[None] * w for _ in range(h)]
    dq = deque()
    for y in range(h):
        for x in range(w):
            if role_grid[y][x] == "blocked":
                dist[y][x] = 0
                dq.append((x, y))
    while dq:
        x, y = dq.popleft()
        d = dist[y][x] + 1
        for dx, dy in _NEIGHBORS:
            nx, ny = x + dx, y + dy
            if 0 <= nx < w and 0 <= ny < h and dist[ny][nx] is None:
                dist[ny][nx] = d
                dq.append((nx, ny))
    return dist


def _coastal_point(meta, primary_local):
    role_grid, w, h = meta["role_grid"], meta["ww"], meta["wh"]
    dist = _distance_to_blocked(role_grid, w, h)
    reach = _reach_from(meta, primary_local)
    best_key, best_cell = None, None
    for (x, y) in reach:
        d = dist[y][x]
        if d is None:
            continue
        key = (d, abs(x - primary_local[0]) + abs(y - primary_local[1]), y, x)
        if best_key is None or key < best_key:
            best_key, best_cell = key, (x, y)
    return best_cell if best_cell is not None else primary_local


def _nearest_global_water(world, gx, gy, radius=8):
    W, H = world["size"]["w"], world["size"]["h"]
    water = world["water"]
    gx = min(max(gx, 0), W - 1)
    gy = min(max(gy, 0), H - 1)
    if water[gy][gx]:
        return (gx, gy)
    seen = {(gx, gy)}
    dq = deque([(gx, gy)])
    while dq:
        x, y = dq.popleft()
        if abs(x - gx) + abs(y - gy) >= radius:
            continue
        for dx, dy in _NEIGHBORS:
            nx, ny = x + dx, y + dy
            if 0 <= nx < W and 0 <= ny < H and (nx, ny) not in seen:
                seen.add((nx, ny))
                if water[ny][nx]:
                    return (nx, ny)
                dq.append((nx, ny))
    return None


def _wire_sea_link(world, places, zone_meta, island_id, ocean_id):
    mi = zone_meta[island_id]
    mo = zone_meta[ocean_id]
    primary_local = mi["site_local"][mi["primary"]["id"]]

    pos_i_raw = _coastal_point(mi, primary_local)
    reach_i = _reach_from(mi, pos_i_raw)
    pos_i = _reserve_near(pos_i_raw, reach_i, mi["used"], mi["ww"], mi["wh"])

    gx, gy = _to_global(mi, pos_i)
    water_pt = _nearest_global_water(world, gx, gy) or (gx, gy)
    approx_o = _to_local(mo, water_pt[0], water_pt[1])
    reach_o = _reach_from(mo, approx_o)
    pos_o = _reserve_near(approx_o, reach_o, mo["used"], mo["ww"], mo["wh"])

    spawn_in_o = _reserve_near(pos_o, reach_o, {pos_o}, mo["ww"], mo["wh"])
    spawn_in_i = _reserve_near(pos_i, reach_i, {pos_i}, mi["ww"], mi["wh"])

    places[island_id]["interactables"].append({
        "id": f"h_sail_{island_id}", "label": "set sail",
        "position": {"cell": {"x": pos_i[0], "y": pos_i[1]}},
        "action": {"type": "move", "target": ocean_id,
                   "spawn": {"cell": {"x": spawn_in_o[0], "y": spawn_in_o[1]}}},
    })
    places[ocean_id]["interactables"].append({
        "id": f"h_landfall_{island_id}", "label": f"landfall at {island_id}",
        "position": {"cell": {"x": pos_o[0], "y": pos_o[1]}},
        "action": {"type": "move", "target": island_id,
                   "spawn": {"cell": {"x": spawn_in_i[0], "y": spawn_in_i[1]}}},
    })


def _wire_interior(places, zone_meta, host_place_id, host_site_id, interior_id):
    mh = zone_meta[host_place_id]
    mi = zone_meta[interior_id]
    host_local = mh["site_local"][host_site_id]
    reach_h = _reach_from(mh, host_local)
    pos_h = _reserve_near(host_local, reach_h, mh["used"], mh["ww"], mh["wh"])
    reach_i = _reach_from(mi, mi["door"])
    pos_i = _reserve_near(mi["door"], reach_i, mi["used"], mi["ww"], mi["wh"])
    spawn_in_i = _reserve_near(pos_i, reach_i, {pos_i}, mi["ww"], mi["wh"])
    spawn_in_h = _reserve_near(pos_h, reach_h, {pos_h}, mh["ww"], mh["wh"])

    places[host_place_id]["interactables"].append({
        "id": f"h_enter_{interior_id}", "label": f"enter {interior_id}",
        "position": {"cell": {"x": pos_h[0], "y": pos_h[1]}},
        "action": {"type": "move", "target": interior_id,
                   "spawn": {"cell": {"x": spawn_in_i[0], "y": spawn_in_i[1]}}},
    })
    places[interior_id]["interactables"].append({
        "id": f"h_exit_to_{host_place_id}", "label": f"leave to {host_place_id}",
        "position": {"cell": {"x": pos_i[0], "y": pos_i[1]}},
        "action": {"type": "move", "target": host_place_id,
                   "spawn": {"cell": {"x": spawn_in_h[0], "y": spawn_in_h[1]}}},
    })
