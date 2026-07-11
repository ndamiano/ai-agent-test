"""Derived points-of-interest: landmark sites scattered between settlements, picked
from terrain signatures (peaks, sheltered bays, biome groves, river mouths) so the
walk between towns has destinations. Pure function of the world dict + a seed/ctx —
never mutates `world`; the caller (generate.py) appends the result to its own sites.
"""
from collections import deque

from .context import WorldCtx

_LAND_AREA_THRESHOLD = 40
_COVE_RADIUS = 2
_COVE_LAND_FRACTION = 0.55
_GROVE_MIN_AREA = 25
_TARGET_PER_LAND_CELLS = 800
_MAX_POIS = 6
_MIN_SPACING = 6

_COAST_KEYWORDS = ("beach", "sand", "shore", "coast", "dune")
_HIGH_KEYWORDS = ("highland", "mountain", "rock", "hill", "peak", "cliff", "crag", "volcan")


def _has_keyword(name, keywords):
    lname = name.lower()
    return any(k in lname for k in keywords)


def _build_ctx(world, seed):
    size = world["size"]
    return WorldCtx(size["w"], size["h"], world["elevation"], world["water"],
                     world["biome"], [], [], seed=seed)


def _resolve_ctx(world, ctx_or_seed):
    if isinstance(ctx_or_seed, WorldCtx):
        return ctx_or_seed
    seed = ctx_or_seed if isinstance(ctx_or_seed, int) else world["meta"]["seed"]
    return _build_ctx(world, seed)


def _blocked_cells(world):
    blocked = set()
    for path in world.get("roads", []):
        for x, y in path:
            blocked.add((x, y))
    for site in world.get("sites", []):
        for cell in site.get("cells") or []:
            blocked.add((cell[0], cell[1]))
        if "x" in site and "y" in site:
            blocked.add((site["x"], site["y"]))
    return blocked


def _existing_site_points(world):
    return [(s["x"], s["y"]) for s in world.get("sites", []) if "x" in s and "y" in s]


def _far_enough(x, y, points, min_dist):
    return all(abs(x - px) + abs(y - py) >= min_dist for px, py in points)


def _peak_candidates(ctx, blocked):
    by_component = {}
    for y in range(ctx.h):
        for x in range(ctx.w):
            if ctx.water[y][x]:
                continue
            cid = ctx.component_id[y][x]
            best = by_component.get(cid)
            if best is None or ctx.elevation[y][x] > best[2]:
                by_component[cid] = (x, y, ctx.elevation[y][x])

    candidates = []
    for cid, (x, y, elev) in by_component.items():
        if ctx.component_size.get(cid, 0) < _LAND_AREA_THRESHOLD:
            continue
        if (x, y) in blocked:
            continue
        candidates.append({"kind": "peak", "x": x, "y": y, "score": elev})
    candidates.sort(key=lambda c: (-c["score"], c["y"], c["x"]))
    return candidates


def _cove_land_fraction(x, y, water, w, h, radius):
    count = 0
    land = 0
    for dy in range(-radius, radius + 1):
        for dx in range(-radius, radius + 1):
            nx, ny = x + dx, y + dy
            if 0 <= nx < w and 0 <= ny < h:
                count += 1
                if not water[ny][nx]:
                    land += 1
    return land / count if count else 0.0


def _bay_candidates(ctx, blocked):
    w, h, water = ctx.w, ctx.h, ctx.water
    cove_mask = [[False] * w for _ in range(h)]
    fraction = [[0.0] * w for _ in range(h)]
    for y in range(h):
        for x in range(w):
            if not water[y][x]:
                continue
            frac = _cove_land_fraction(x, y, water, w, h, _COVE_RADIUS)
            if frac >= _COVE_LAND_FRACTION:
                cove_mask[y][x] = True
                fraction[y][x] = frac

    seen = set()
    clusters = []
    for y in range(h):
        for x in range(w):
            if not cove_mask[y][x] or (x, y) in seen:
                continue
            seen.add((x, y))
            q = deque([(x, y)])
            cells = [(x, y)]
            while q:
                cx, cy = q.popleft()
                for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    nx, ny = cx + dx, cy + dy
                    if (0 <= nx < w and 0 <= ny < h and cove_mask[ny][nx]
                            and (nx, ny) not in seen):
                        seen.add((nx, ny))
                        cells.append((nx, ny))
                        q.append((nx, ny))
            clusters.append(cells)

    candidates = []
    for cells in clusters:
        cells.sort(key=lambda c: (-fraction[c[1]][c[0]], c[1], c[0]))
        best_score = fraction[cells[0][1]][cells[0][0]]
        land_spot = None
        for cx, cy in cells:
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                nx, ny = cx + dx, cy + dy
                if (0 <= nx < w and 0 <= ny < h and not water[ny][nx]
                        and (nx, ny) not in blocked):
                    if land_spot is None or (ny, nx) < (land_spot[1], land_spot[0]):
                        land_spot = (nx, ny)
            if land_spot is not None:
                break
        if land_spot is None:
            continue
        candidates.append({"kind": "bay", "x": land_spot[0], "y": land_spot[1], "score": best_score})
    candidates.sort(key=lambda c: (-c["score"], c["y"], c["x"]))
    return candidates


def _interior_point(patch_cells, w, h):
    patch_set = set(patch_cells)
    min_x = min(c[0] for c in patch_cells) - 1
    max_x = max(c[0] for c in patch_cells) + 1
    min_y = min(c[1] for c in patch_cells) - 1
    max_y = max(c[1] for c in patch_cells) + 1
    bw, bh = max_x - min_x + 1, max_y - min_y + 1
    dist = [[-1] * bw for _ in range(bh)]
    q = deque()
    for by in range(bh):
        for bx in range(bw):
            x, y = min_x + bx, min_y + by
            if (x, y) not in patch_set:
                dist[by][bx] = 0
                q.append((bx, by))
    while q:
        bx, by = q.popleft()
        d = dist[by][bx] + 1
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nbx, nby = bx + dx, by + dy
            if 0 <= nbx < bw and 0 <= nby < bh and dist[nby][nbx] == -1:
                dist[nby][nbx] = d
                q.append((nbx, nby))

    best = None
    for x, y in patch_cells:
        bx, by = x - min_x, y - min_y
        d = dist[by][bx]
        if best is None or d > best[0] or (d == best[0] and (y, x) < (best[2], best[1])):
            best = (d, x, y)
    return best[1], best[2]


def _grove_candidates(ctx, blocked):
    w, h, biome, water = ctx.w, ctx.h, ctx.biome, ctx.water
    land_biome_names = {biome[y][x] for y in range(h) for x in range(w) if not water[y][x]}
    mid_biomes = [b for b in land_biome_names
                  if not _has_keyword(b, _COAST_KEYWORDS) and not _has_keyword(b, _HIGH_KEYWORDS)]

    candidates = []
    for biome_name in sorted(mid_biomes):
        seen = set()
        best_patch = None
        for y in range(h):
            for x in range(w):
                if water[y][x] or biome[y][x] != biome_name or (x, y) in seen:
                    continue
                seen.add((x, y))
                q = deque([(x, y)])
                cells = [(x, y)]
                while q:
                    cx, cy = q.popleft()
                    for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                        nx, ny = cx + dx, cy + dy
                        if (0 <= nx < w and 0 <= ny < h and not water[ny][nx]
                                and biome[ny][nx] == biome_name and (nx, ny) not in seen):
                            seen.add((nx, ny))
                            cells.append((nx, ny))
                            q.append((nx, ny))
                if best_patch is None or len(cells) > len(best_patch):
                    best_patch = cells
        if best_patch is None or len(best_patch) < _GROVE_MIN_AREA:
            continue
        ix, iy = _interior_point(best_patch, w, h)
        if (ix, iy) in blocked:
            continue
        candidates.append({"kind": "grove", "x": ix, "y": iy, "score": len(best_patch)})
    candidates.sort(key=lambda c: (-c["score"], c["y"], c["x"]))
    return candidates


def _river_mouth_candidates(world, ctx, blocked):
    candidates = []
    for path in world.get("rivers", []):
        if not path:
            continue
        mx, my = path[-1]
        spot = None
        if 0 <= mx < ctx.w and 0 <= my < ctx.h and not ctx.water[my][mx]:
            spot = (mx, my)
        else:
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                nx, ny = mx + dx, my + dy
                if (0 <= nx < ctx.w and 0 <= ny < ctx.h and not ctx.water[ny][nx]):
                    spot = (nx, ny)
                    break
        if spot is None or spot in blocked:
            continue
        candidates.append({"kind": "river_mouth", "x": spot[0], "y": spot[1], "score": 0.0})
    candidates.sort(key=lambda c: (c["y"], c["x"]))
    return candidates


def derive_pois(world, ctx_or_seed=None):
    """Scatter a small set of landmark POIs from terrain signatures. Returns a list of
    site dicts shaped like `world["sites"]` entries; does not mutate `world`."""
    ctx = _resolve_ctx(world, ctx_or_seed)
    blocked = _blocked_cells(world)
    existing_points = _existing_site_points(world)

    land_cells = sum(1 for y in range(ctx.h) for x in range(ctx.w) if not ctx.water[y][x])
    target = min(_MAX_POIS, land_cells // _TARGET_PER_LAND_CELLS)
    if target <= 0:
        return []

    by_kind = {
        "peak": _peak_candidates(ctx, blocked),
        "bay": _bay_candidates(ctx, blocked),
        "grove": _grove_candidates(ctx, blocked),
        "river_mouth": _river_mouth_candidates(world, ctx, blocked) if "rivers" in world else [],
    }
    kind_order = ["peak", "bay", "grove", "river_mouth"]
    indices = {k: 0 for k in kind_order}

    selected = []
    selected_points = list(existing_points)
    counters = {}
    progressed = True
    while len(selected) < target and progressed:
        progressed = False
        for kind in kind_order:
            if len(selected) >= target:
                break
            candidates = by_kind[kind]
            i = indices[kind]
            while i < len(candidates):
                cand = candidates[i]
                i += 1
                x, y = cand["x"], cand["y"]
                if (x, y) in blocked:
                    continue
                if not _far_enough(x, y, selected_points, _MIN_SPACING):
                    continue
                counters[kind] = counters.get(kind, 0) + 1
                site_id = f"poi_{kind}_{counters[kind]}"
                selected.append({"id": site_id, "type": "landmark", "x": x, "y": y,
                                 "derived": True})
                selected_points.append((x, y))
                blocked.add((x, y))
                progressed = True
                break
            indices[kind] = i

    return selected
