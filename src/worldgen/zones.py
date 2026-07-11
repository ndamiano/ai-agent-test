"""Carve the macro world into playable `places` zones (see docs/game_ir.schema.json).

One walkable map per real discontinuity: a landmass with at least one located site becomes ONE
place spanning its whole bounding box (padded, UPSAMPLED k x from the continuous macro fields
via detail.sample — fractional-coordinate fBm resampling, so the coastline gains real fractal
detail at zone scale), so a continuous island is walked, never diced into arbitrary per-site
crops. Sites on that landmass are in-map examine markers, not exits — two beaches on the same
island are a walk, not a load screen. A single ocean place (the whole world, water/land
inverted) is the only thing that crosses a real discontinuity (open water between landmasses);
it exists only when the graph actually needs it (an authored open_water site, or more than one
landmass zone). Role (open/blocked) comes from the resampled water line, which detail.sample
re-thresholds ONLY inside the macro coast band — interior land can never turn to water, and
site anchors/roads are force-landed, with a carve-repair pass guaranteeing every anchor stays
reachable from the primary site. Each place declares its scale (`m_per_cell`) so the tiers
read consistently. Interior sites get a small fixed room instead of a world sample.
Connectivity (`move` interactables) is derived from geometry, never authored.

A settlement is a real tier, not a flag: its macro footprint renders as blocked rooftops on the
landmass map (a town you walk up to, entered at a gate hotspot beside it), and the settlement id
names a dedicated fine-resolution town place (towns.build_town: streets, plaza, buildings).
Interiors hosted at a settlement wire through their own building's doorstep inside the town —
never a pile of markers on the world map. The landmass place takes a derived `<primary>_region`
id so the settlement id stays the story-facing town.
"""

from collections import deque

from . import detail, towns

_MAX_FINE = 256
_MAX_OCEAN_W, _MAX_OCEAN_H = 64, 48
_ZONE_PAD = 2
_MACRO_CELL_M = 60.0
_TOWN_CELL_M = 2.0
_RIVER_THEME = "river water"
_ROAD_THEME = "worn path"
_TOWN_THEME = "town rooftops"
_TOWN_LABEL = "walled town"
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


    comp = _components(world)
    bboxes = _all_bboxes(comp, W, H)

    land_sites = [s for s in located if not water[s["y"]][s["x"]]]
    water_sites = [s for s in located if water[s["y"]][s["x"]]]

    landmass_groups = {}
    for s in land_sites:
        landmass_groups.setdefault(comp[s["y"]][s["x"]], []).append(s)
    # A derived POI never justifies a zone by itself — a landmass earns a walkable map
    # only when the recipe put something there.
    landmass_groups = {cid: group for cid, group in landmass_groups.items()
                       if not all(s.get("derived") for s in group)}

    settlement_sites = {s["id"]: s for s in located if s["type"] == "settlement"}
    hosted = {sid: [] for sid in settlement_sites}
    for s in interiors:
        if s["host"] in hosted:
            hosted[s["host"]].append(s["id"])
    towns_by_site = {sid: towns.build_town(site, hosted[sid], world, seed_base)
                     for sid, site in settlement_sites.items()}

    places = {}
    zone_meta = {}
    site_place_id = {}
    town_region = {}
    landmass_pid = {}

    for cid, group in landmass_groups.items():
        primary = next((s for s in group if s["type"] == "settlement"), group[0])
        pid = f"{primary['id']}_region" if primary["type"] == "settlement" else primary["id"]

        x0, x1, y0, y1 = bboxes[cid]
        wx0, wy0, ww0, wh0 = _pad_bbox((x0, x1, y0, y1), W, H, _ZONE_PAD)
        k = max(1, min(3, _MAX_FINE // ww0, _MAX_FINE // wh0))

        theme_g, role_grid, elev_rows, ww, wh = _sample_landmass(
            world, recipe, seed_base, wx0, wy0, ww0, wh0, k, _collect_site_cells(group))

        meta = {"wx0": wx0, "wy0": wy0, "f": float(k), "ww": ww, "wh": wh,
                "role_grid": role_grid, "used": set(), "site_local": {}, "primary": primary}
        zone_meta[pid] = meta
        landmass_pid[cid] = pid

        footprints = {}
        for s in group:
            if s["type"] != "settlement":
                continue
            local_cells = set()
            for gx, gy in (s.get("cells") or [[s["x"], s["y"]]]):
                if wx0 <= gx < wx0 + ww0 and wy0 <= gy < wy0 + wh0:
                    bx, by = (gx - wx0) * k, (gy - wy0) * k
                    local_cells.update((bx + dx, by + dy) for dy in range(k) for dx in range(k))
            blob = _block_footprint(role_grid, theme_g, local_cells, ww, wh)
            box_cells = blob or local_cells
            if box_cells:
                bx0 = min(c[0] for c in box_cells)
                by0 = min(c[1] for c in box_cells)
                footprints[s["id"]] = {
                    "x": bx0, "y": by0,
                    "w": max(c[0] for c in box_cells) - bx0 + 1,
                    "h": max(c[1] for c in box_cells) - by0 + 1,
                    "label": _TOWN_LABEL,
                }

        rows, legend = _paint(theme_g, role_grid, ww, wh)

        interactables = []
        for s in group:
            local = _to_local(meta, s["x"], s["y"])
            meta["site_local"][s["id"]] = local
            reach = _reach_from(meta, local)
            cell = _reserve_near(local, reach, meta["used"], ww, wh)
            if s["type"] == "settlement":
                town = towns_by_site[s["id"]]
                inward = town["gates"][0]["inward"]
                if s["id"] in footprints:
                    footprints[s["id"]]["door"] = [cell[0], cell[1]]
                interactables.append({
                    "id": f"h_enter_{s['id']}",
                    "label": f"enter {s['id'].replace('_', ' ')}",
                    "position": {"cell": {"x": cell[0], "y": cell[1]}},
                    "action": {"type": "move", "target": s["id"],
                               "spawn": {"cell": {"x": inward[0], "y": inward[1]}}},
                })
                site_place_id[s["id"]] = s["id"]
                town_region[s["id"]] = (pid, cell)
            else:
                interactables.append(_examine_hotspot(s, cell))
                site_place_id[s["id"]] = pid

        places[pid] = {
            "kind": "world_map",
            "tiles": {"rows": rows, "legend": legend},
            "interactables": interactables,
            "m_per_cell": _MACRO_CELL_M / k,
            "elevation": elev_rows,
            "sea_level": detail.sea_level(world, recipe),
            "layout": {"window": [wx0, wy0, ww0, wh0]},
        }
        if footprints:
            places[pid]["footprints"] = footprints

    for sid, town in towns_by_site.items():
        region_pid, enter_cell = town_region[sid]
        rmeta = zone_meta[region_pid]
        tmeta = {"ww": town["ww"], "wh": town["wh"], "role_grid": town["role_grid"],
                 "used": set(town["doors"].values()) | {g["cell"] for g in town["gates"]},
                 "doors": town["doors"], "plaza": town["plaza"]}
        zone_meta[sid] = tmeta

        reach_r = _reach_from(rmeta, enter_cell)
        spawn_r = _reserve_near(enter_cell, reach_r, set(rmeta["used"]), rmeta["ww"],
                                rmeta["wh"])
        interactables = []
        for gate in town["gates"]:
            gx, gy = gate["cell"]
            interactables.append({
                "id": f"h_leave_{sid}_{gate['edge']}",
                "label": f"leave {sid.replace('_', ' ')}",
                "position": {"cell": {"x": gx, "y": gy}},
                "action": {"type": "move", "target": region_pid,
                           "spawn": {"cell": {"x": spawn_r[0], "y": spawn_r[1]}}},
            })

        places[sid] = {
            "kind": "town",
            "tiles": town["tiles"],
            "interactables": interactables,
            "footprints": town["footprints"],
            "m_per_cell": _TOWN_CELL_M,
        }

    open_water_sites = [s for s in water_sites if s["type"] == "open_water"]
    need_ocean = bool(water_sites) or len(landmass_groups) > 1
    if need_ocean:
        ocean_id = open_water_sites[0]["id"] if open_water_sites else (
            water_sites[0]["id"] if water_sites else "open_sea")
        scale_o = 2 if (W > _MAX_OCEAN_W or H > _MAX_OCEAN_H) else 1

        theme_o, role_o, elev_o, oww, owh = _sample_ocean(world, scale_o)
        rows_o, legend_o = _paint(theme_o, role_o, oww, owh)

        ometa = {"wx0": 0, "wy0": 0, "f": 1.0 / scale_o, "ww": oww, "wh": owh,
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
            "m_per_cell": _MACRO_CELL_M * scale_o,
            "elevation": elev_o,
            "sea_level": detail.sea_level(world, recipe),
            "layout": {"window": [0, 0, W, H]},
        }

        for cid in landmass_groups:
            _wire_sea_link(world, places, zone_meta, landmass_pid[cid], ocean_id)

    for site in interiors:
        rows, legend = _interior_room()
        role_grid = [[legend[ch]["role"] for ch in row] for row in rows]
        center = (_IROOM_W // 2, _IROOM_H // 2)
        door = (_IROOM_W // 2, _IROOM_H - 2)
        places[site["id"]] = {
            "kind": "interior",
            "tiles": {"rows": rows, "legend": legend},
            "interactables": [_examine_hotspot(site, center)],
            "m_per_cell": _TOWN_CELL_M,
        }
        zone_meta[site["id"]] = {"ww": _IROOM_W, "wh": _IROOM_H, "role_grid": role_grid,
                                  "used": {center}, "door": door}

    for site in interiors:
        if site["host"] in towns_by_site:
            _wire_town_interior(places, zone_meta, site["host"], site["id"])
        else:
            host_place = site_place_id[site["host"]]
            _wire_interior(places, zone_meta, host_place, site["host"], site["id"])

    settlement = next(s for s in sites if s["type"] == "settlement")
    start_place = settlement["id"]
    smeta = zone_meta[start_place]
    reach = _reach_from(smeta, smeta["plaza"])
    start_spawn = _reserve_near(smeta["plaza"], reach, set(smeta["used"]), smeta["ww"],
                                smeta["wh"])

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


# ── zone sampling: landmass = k x fBm upsample; ocean = crop + 2x downsample ──
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


def _sample_landmass(world, recipe, seed_base, wx0, wy0, ww0, wh0, k, group_site_cells):
    """Resample the window at k x resolution from the continuous fields, then stamp the
    macro linework back on as fine polylines: rivers (blocked overlay), roads (open — a
    road crossing a river IS the bridge), and site anchors (forced open land). A final
    carve pass guarantees every anchor is reachable from the first one, so coastline/river
    detail can never sever the zone graph."""
    theme, water_f, elev_f = detail.sample(world, recipe, seed_base, wx0, wy0, ww0, wh0, k)
    fw, fh = ww0 * k, wh0 * k
    sea = detail.sea_level(world, recipe)

    role = [["blocked" if water_f[y][x] else "open" for x in range(fw)] for y in range(fh)]

    def force_land(x, y):
        role[y][x] = "open"
        if water_f[y][x] or theme[y][x] == _RIVER_THEME:
            theme[y][x] = _land_theme_near(theme, water_f, fw, fh, x, y)
        water_f[y][x] = False
        if elev_f[y][x] < sea:
            elev_f[y][x] = sea + 0.01

    for path in world.get("rivers", []):
        for x, y in _fine_polyline(path, wx0, wy0, k, fw, fh):
            if not water_f[y][x]:
                theme[y][x] = _RIVER_THEME
                role[y][x] = "blocked"

    for path in world["roads"]:
        for x, y in _fine_polyline(path, wx0, wy0, k, fw, fh):
            water_f[y][x] = False
            theme[y][x] = _ROAD_THEME
            role[y][x] = "open"
            if elev_f[y][x] < sea:
                elev_f[y][x] = sea + 0.01

    anchors = []
    for gx, gy in sorted(group_site_cells):
        if wx0 <= gx < wx0 + ww0 and wy0 <= gy < wy0 + wh0 and not world["water"][gy][gx]:
            x, y = (gx - wx0) * k + k // 2, (gy - wy0) * k + k // 2
            force_land(x, y)
            anchors.append((x, y))

    _carve_connected(theme, role, water_f, elev_f, anchors, fw, fh, sea)
    return theme, role, elev_f, fw, fh


def _sample_ocean(world, scale):
    """The whole world, water walkable and land blocked, halved when the world is large.
    Sea travel is symbolic: raw macro themes, no re-detail."""
    W, H = world["size"]["w"], world["size"]["h"]
    water = [[bool(world["water"][y][x]) for x in range(W)] for y in range(H)]
    theme = [[world["biome"][y][x] for x in range(W)] for y in range(H)]
    elev = [[float(world["elevation"][y][x]) for x in range(W)] for y in range(H)]

    if scale == 2:
        water_g = _downsample_majority(water, W, H)
        theme_g = _downsample_categorical_matching(theme, water, water_g, W, H)
        elev_g = _downsample_average(elev, W, H)
    else:
        water_g, theme_g, elev_g = water, theme, elev
    ow, oh = len(water_g[0]), len(water_g)

    role = [["open" if water_g[y][x] else "blocked" for x in range(ow)] for y in range(oh)]
    return theme_g, role, elev_g, ow, oh


def _bresenham(x0, y0, x1, y1):
    cells = []
    dx, dy = abs(x1 - x0), -abs(y1 - y0)
    sx, sy = (1 if x0 < x1 else -1), (1 if y0 < y1 else -1)
    err = dx + dy
    while True:
        cells.append((x0, y0))
        if x0 == x1 and y0 == y1:
            return cells
        e2 = 2 * err
        if e2 >= dy:
            err += dy
            x0 += sx
        if e2 <= dx:
            err += dx
            y0 += sy


def _fine_polyline(path, wx0, wy0, k, fw, fh):
    pts = [((x - wx0) * k + k // 2, (y - wy0) * k + k // 2) for x, y in path]
    cells = list(pts[:1])
    for (ax, ay), (bx, by) in zip(pts, pts[1:]):
        cells.extend(_bresenham(ax, ay, bx, by))
    return [(x, y) for x, y in cells if 0 <= x < fw and 0 <= y < fh]


def _land_theme_near(theme, water_f, fw, fh, sx, sy):
    seen = {(sx, sy)}
    dq = deque(seen)
    while dq:
        x, y = dq.popleft()
        if not water_f[y][x] and theme[y][x] not in (_RIVER_THEME, _ROAD_THEME):
            return theme[y][x]
        for dx, dy in _NEIGHBORS:
            nxt = (x + dx, y + dy)
            if 0 <= nxt[0] < fw and 0 <= nxt[1] < fh and nxt not in seen:
                seen.add(nxt)
                dq.append(nxt)
    return "beach"


def _carve_connected(theme, role, water_f, elev_f, anchors, fw, fh, sea):
    """Every anchor must reach the first one over open cells; an unreachable anchor gets a
    1-wide land path carved to the reachable component (shortest BFS line over any cells)."""
    if len(anchors) < 2:
        return
    for a in anchors[1:]:
        flood = _flood(role, fw, fh, anchors[0])
        if a in flood:
            continue
        prev = {a: None}
        dq = deque([a])
        hit = None
        while dq and hit is None:
            x, y = dq.popleft()
            for dx, dy in _NEIGHBORS:
                nxt = (x + dx, y + dy)
                if 0 <= nxt[0] < fw and 0 <= nxt[1] < fh and nxt not in prev:
                    prev[nxt] = (x, y)
                    if nxt in flood:
                        hit = nxt
                        break
                    dq.append(nxt)
        cell = hit
        while cell is not None:
            x, y = cell
            if role[y][x] != "open":
                role[y][x] = "open"
                if water_f[y][x] or theme[y][x] == _RIVER_THEME:
                    theme[y][x] = _land_theme_near(theme, water_f, fw, fh, x, y)
                water_f[y][x] = False
                if elev_f[y][x] < sea:
                    elev_f[y][x] = sea + 0.01
            cell = prev[cell]


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


def _block_footprint(role_grid, theme_grid, cells, ww, wh):
    """Turn a settlement's macro footprint into blocked rooftop cells on the landmass map — a
    town is an obstacle you walk up to, entered at its gate hotspot. Road cells stay open (the
    road runs to the gate) and the whole block is reverted if it would sever the open walk
    graph around it (theme untouched then, so no theme ever carries both roles)."""
    blob = {(x, y) for x, y in cells
            if 0 <= x < ww and 0 <= y < wh
            and role_grid[y][x] == "open" and theme_grid[y][x] != _ROAD_THEME}
    if not blob:
        return set()
    for x, y in blob:
        role_grid[y][x] = "blocked"
    perimeter = {(x + dx, y + dy) for x, y in blob for dx, dy in _NEIGHBORS
                 if 0 <= x + dx < ww and 0 <= y + dy < wh
                 and role_grid[y + dy][x + dx] == "open"}
    if perimeter:
        seen = _flood(role_grid, ww, wh, next(iter(perimeter)))
        if not perimeter <= seen:
            for x, y in blob:
                role_grid[y][x] = "open"
            return set()
    for x, y in blob:
        theme_grid[y][x] = _TOWN_THEME
    return blob


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
    f = meta["f"]
    off = int(f) // 2
    lx = int((gx - meta["wx0"]) * f) + off
    ly = int((gy - meta["wy0"]) * f) + off
    lx = min(max(lx, 0), meta["ww"] - 1)
    ly = min(max(ly, 0), meta["wh"] - 1)
    return (lx, ly)


def _to_global(meta, cell):
    lx, ly = cell
    f = meta["f"]
    return (meta["wx0"] + int(lx / f), meta["wy0"] + int(ly / f))


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
    """Nearest free cell to `target` within `allowed`, preferring cells not 4-adjacent to an
    already-used one — two hotspots never end up side by side unless the blob is too tight to
    avoid it."""
    for spaced in (True, False):
        cell = _free_near(target, allowed, used, w, h, spaced)
        if cell is not None:
            used.add(cell)
            return cell
    raise ValueError("no free reachable cell to place an interactable")


def _free_near(target, allowed, used, w, h, spaced):
    def ok(cell):
        if cell not in allowed or cell in used:
            return False
        if spaced and any((cell[0] + dx, cell[1] + dy) in used for dx, dy in _NEIGHBORS):
            return False
        return True

    tx, ty = target
    if 0 <= tx < w and 0 <= ty < h and ok((tx, ty)):
        return (tx, ty)
    seen = {(tx, ty)}
    dq = deque(seen)
    while dq:
        x, y = dq.popleft()
        for dx, dy in _NEIGHBORS:
            nxt = (x + dx, y + dy)
            if 0 <= nxt[0] < w and 0 <= nxt[1] < h and nxt not in seen:
                seen.add(nxt)
                if ok(nxt):
                    return nxt
                dq.append(nxt)
    return None


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


def _largest_open_component(meta):
    role, w, h = meta["role_grid"], meta["ww"], meta["wh"]
    seen_all = set()
    best = set()
    for y in range(h):
        for x in range(w):
            if role[y][x] == "open" and (x, y) not in seen_all:
                comp = _flood(role, w, h, (x, y))
                seen_all |= comp
                if len(comp) > len(best):
                    best = comp
    return best


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
    # Landfall always lands on the open sea — the nearest water blob may be a lake
    # (lakes are in the water mask) far too small to hold a hotspot + spawn.
    reach_o = _largest_open_component(mo)
    pos_o = _reserve_near(approx_o, reach_o, mo["used"], mo["ww"], mo["wh"])

    spawn_in_o = _reserve_near(pos_o, reach_o, set(mo["used"]), mo["ww"], mo["wh"])
    spawn_in_i = _reserve_near(pos_i, reach_i, set(mi["used"]), mi["ww"], mi["wh"])

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


def _wire_town_interior(places, zone_meta, town_id, interior_id):
    """The interior's building doorstep IS the enter hotspot — stepping onto the door of the
    tavern building enters the tavern. Doorsteps are pre-reserved by the town generator, so
    two interiors can never share or crowd a cell."""
    mt = zone_meta[town_id]
    mi = zone_meta[interior_id]
    door = mt["doors"].get(interior_id)
    if door is None:
        reach = _reach_from(mt, mt["plaza"])
        door = _reserve_near(mt["plaza"], reach, mt["used"], mt["ww"], mt["wh"])
    reach_t = _reach_from(mt, door)
    spawn_in_t = _reserve_near(door, reach_t, set(mt["used"]), mt["ww"], mt["wh"])
    reach_i = _reach_from(mi, mi["door"])
    pos_i = _reserve_near(mi["door"], reach_i, mi["used"], mi["ww"], mi["wh"])
    spawn_in_i = _reserve_near(pos_i, reach_i, set(mi["used"]), mi["ww"], mi["wh"])

    places[town_id]["interactables"].append({
        "id": f"h_enter_{interior_id}", "label": f"enter {interior_id.replace('_', ' ')}",
        "position": {"cell": {"x": door[0], "y": door[1]}},
        "action": {"type": "move", "target": interior_id,
                   "spawn": {"cell": {"x": spawn_in_i[0], "y": spawn_in_i[1]}}},
    })
    places[interior_id]["interactables"].append({
        "id": f"h_exit_to_{town_id}", "label": f"leave to {town_id.replace('_', ' ')}",
        "position": {"cell": {"x": pos_i[0], "y": pos_i[1]}},
        "action": {"type": "move", "target": town_id,
                   "spawn": {"cell": {"x": spawn_in_t[0], "y": spawn_in_t[1]}}},
    })


def _wire_interior(places, zone_meta, host_place_id, host_site_id, interior_id):
    mh = zone_meta[host_place_id]
    mi = zone_meta[interior_id]
    host_local = mh["site_local"][host_site_id]
    reach_h = _reach_from(mh, host_local)
    pos_h = _reserve_near(host_local, reach_h, mh["used"], mh["ww"], mh["wh"])
    reach_i = _reach_from(mi, mi["door"])
    pos_i = _reserve_near(mi["door"], reach_i, mi["used"], mi["ww"], mi["wh"])
    spawn_in_i = _reserve_near(pos_i, reach_i, set(mi["used"]), mi["ww"], mi["wh"])
    spawn_in_h = _reserve_near(pos_h, reach_h, set(mh["used"]), mh["ww"], mh["wh"])

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
