import heapq

_D4 = ((1, 0), (-1, 0), (0, 1), (0, -1))
_D8 = _D4 + ((1, 1), (1, -1), (-1, 1), (-1, -1))

_SOURCE_ELEVATION_QUANTILE = 0.85
_LAKE_CAP = 60


def _river_count(w, h):
    return max(2, min(5, (w * h) // 5000))


def _source_min_distance(w, h):
    return max(6.0, min(w, h) * 0.18)


def _select_sources(elevation, water, w, h, blocked, rng, count, min_dist):
    land = [(x, y) for y in range(h) for x in range(w)
            if not water[y][x] and (x, y) not in blocked]
    if not land:
        return []
    elevations = sorted(elevation[y][x] for x, y in land)
    cutoff = elevations[int(_SOURCE_ELEVATION_QUANTILE * (len(elevations) - 1))]
    candidates = [(x, y) for x, y in land if elevation[y][x] >= cutoff]
    candidates.sort(key=lambda c: (-elevation[c[1]][c[0]], rng.random()))

    sources = []
    for x, y in candidates:
        if all(((x - sx) ** 2 + (y - sy) ** 2) ** 0.5 >= min_dist for sx, sy in sources):
            sources.append((x, y))
        if len(sources) >= count:
            break
    return sources


def _flood_lake(px, py, elevation, water, w, h, blocked, local_water, cap):
    """Priority-flood the pit at (px, py): pop the lowest-elevation basin cell
    each round (so the popped elevation is the current water level) until a
    neighbor lower than that level is found — the spill point. Cells popped
    become the lake; a basin that never spills within `cap` aborts (None)."""
    in_basin = {(px, py)}
    popped = set()
    heap = [(elevation[py][px], px, py)]
    while heap:
        elev, x, y = heapq.heappop(heap)
        if (x, y) in popped:
            continue
        popped.add((x, y))
        if len(popped) > cap:
            return None, None
        for dx, dy in _D4:
            nx, ny = x + dx, y + dy
            if not (0 <= nx < w and 0 <= ny < h):
                continue
            if (nx, ny) in blocked or (nx, ny) in popped:
                continue
            if water[ny][nx] or (nx, ny) in local_water or elevation[ny][nx] < elev:
                return popped, (nx, ny)
            if (nx, ny) not in in_basin:
                in_basin.add((nx, ny))
                heapq.heappush(heap, (elevation[ny][nx], nx, ny))
    return None, None


def _trace_river(sx, sy, elevation, water, w, h, blocked, rng, max_steps):
    """Steepest-descent trace with rng tie-break. Returns (path, lake_basins)
    on success, or (None, None) if the trace is stuck, cycles, or a pit's
    flood runs away — the caller drops that river rather than erroring."""
    path = [(sx, sy)]
    visited = {(sx, sy)}
    local_water = set()
    lakes = []
    x, y = sx, sy

    for _ in range(max_steps):
        if water[y][x] or (x, y) in local_water:
            return path, lakes

        neighbors = [(x + dx, y + dy) for dx, dy in _D8
                     if 0 <= x + dx < w and 0 <= y + dy < h
                     and (x + dx, y + dy) not in blocked]
        if not neighbors:
            return None, None

        lowest = min(elevation[ny][nx] for nx, ny in neighbors)
        if lowest >= elevation[y][x]:
            # Exclude the path-so-far too: a later pit's basin must never
            # reclaim an earlier stretch of this same river as lake.
            basin, spill = _flood_lake(x, y, elevation, water, w, h, blocked | visited,
                                        local_water, _LAKE_CAP)
            if basin is None:
                return None, None
            local_water.update(basin)
            lakes.append(sorted(basin))
            nx, ny = spill
            if (nx, ny) in visited:
                return None, None
            x, y = nx, ny
            path.append((x, y))
            visited.add((x, y))
            continue

        tied = [n for n in neighbors if elevation[n[1]][n[0]] == lowest]
        nxt = tied[0] if len(tied) == 1 else tied[rng.randrange(len(tied))]
        if nxt in visited:
            return None, None
        x, y = nxt
        path.append((x, y))
        visited.add((x, y))

    return None, None


def carve(elevation, water, w, h, rng, avoid_cells=frozenset()):
    """Traces rivers from high ground down to the sea, flooding undrainable
    pits into lakes along the way. Lakes are merged into `water` (real water
    bodies); rivers are returned as a separate overlay so landmass
    connectivity (computed over the water mask) is unaffected by them.
    Mutates and returns `water`."""
    blocked = set(avoid_cells)
    count = _river_count(w, h)
    min_dist = _source_min_distance(w, h)
    sources = _select_sources(elevation, water, w, h, blocked, rng, count, min_dist)

    rivers = []
    lakes = []
    max_steps = w * h

    for sx, sy in sources:
        if (sx, sy) in blocked or water[sy][sx]:
            continue
        path, river_lakes = _trace_river(sx, sy, elevation, water, w, h, blocked, rng, max_steps)
        if path is None or len(path) < 2:
            continue

        for basin in river_lakes:
            for bx, by in basin:
                water[by][bx] = True
            lakes.append([[bx, by] for bx, by in basin])
        rivers.append([[x, y] for x, y in path])
        blocked.update(path)

    return rivers, lakes, water
