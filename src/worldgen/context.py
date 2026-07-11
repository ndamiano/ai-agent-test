from collections import deque


class WorldCtx:
    """Precomputed lenses over the terrain, shared by placement + scoring stages."""

    def __init__(self, w, h, elevation, water, biome, land_biomes, water_biomes, seed=0):
        self.w = w
        self.h = h
        self.seed = seed
        self.elevation = elevation
        self.water = water
        self.biome = biome
        self.land_biomes = land_biomes
        self.water_biomes = water_biomes
        self.diag = (w * w + h * h) ** 0.5
        self.water_dist = _distance_from_water(w, h, water)
        self.component_id, self.component_size = _label_components(w, h, water)

    def in_bounds(self, x, y):
        return 0 <= x < self.w and 0 <= y < self.h


def _distance_from_water(w, h, water):
    dist = [[-1] * w for _ in range(h)]
    q = deque()
    for y in range(h):
        for x in range(w):
            if water[y][x]:
                dist[y][x] = 0
                q.append((x, y))
    while q:
        x, y = q.popleft()
        d = dist[y][x] + 1
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nx, ny = x + dx, y + dy
            if 0 <= nx < w and 0 <= ny < h and dist[ny][nx] == -1:
                dist[ny][nx] = d
                q.append((nx, ny))
    return dist


def _label_components(w, h, water):
    comp_id = [[-1] * w for _ in range(h)]
    comp_size = {}
    next_id = 0
    for sy in range(h):
        for sx in range(w):
            if comp_id[sy][sx] != -1:
                continue
            kind = water[sy][sx]
            cid = next_id
            next_id += 1
            q = deque([(sx, sy)])
            comp_id[sy][sx] = cid
            size = 0
            while q:
                x, y = q.popleft()
                size += 1
                for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    nx, ny = x + dx, y + dy
                    if 0 <= nx < w and 0 <= ny < h and comp_id[ny][nx] == -1 and water[ny][nx] == kind:
                        comp_id[ny][nx] = cid
                        q.append((nx, ny))
            comp_size[cid] = size
    return comp_id, comp_size


def connected_cells(w, h, water, start, kind, cap):
    """BFS blob of up to `cap` cells of the given water-ness, from start, for footprints."""
    sx, sy = start
    if water[sy][sx] != kind:
        return [(sx, sy)]
    seen = {(sx, sy)}
    q = deque([(sx, sy)])
    out = [(sx, sy)]
    while q and len(out) < cap:
        x, y = q.popleft()
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nx, ny = x + dx, y + dy
            if (0 <= nx < w and 0 <= ny < h and (nx, ny) not in seen
                    and water[ny][nx] == kind):
                seen.add((nx, ny))
                out.append((nx, ny))
                q.append((nx, ny))
                if len(out) >= cap:
                    break
    return out
