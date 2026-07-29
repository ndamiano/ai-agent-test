from collections import deque

_TARGET_LAND_FRACTION = {"archipelago": 0.30, "continent": 0.55}
_PUDDLE_MAX_CELLS = 5
_ISLET_MAX_CELLS = 8


def sea_level_for(elevation, w, h, archetype):
    target_land = _TARGET_LAND_FRACTION[archetype]
    flat = sorted(v for row in elevation for v in row)
    idx = int((1 - target_land) * len(flat))
    idx = max(0, min(len(flat) - 1, idx))
    return flat[idx]


def build_mask(elevation, w, h, sea_level):
    return [[elevation[y][x] < sea_level for x in range(w)] for y in range(h)]


def fill_puddles(elevation, water, w, h, sea_level):
    """Convert small water pockets (noise artifacts) into land."""
    seen = [[False] * w for _ in range(h)]
    for sy in range(h):
        for sx in range(w):
            if water[sy][sx] and not seen[sy][sx]:
                cells = []
                q = deque([(sx, sy)])
                seen[sy][sx] = True
                while q:
                    x, y = q.popleft()
                    cells.append((x, y))
                    for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                        nx, ny = x + dx, y + dy
                        if 0 <= nx < w and 0 <= ny < h and water[ny][nx] and not seen[ny][nx]:
                            seen[ny][nx] = True
                            q.append((nx, ny))
                if len(cells) <= _PUDDLE_MAX_CELLS:
                    for x, y in cells:
                        water[y][x] = False
                        elevation[y][x] = sea_level + 0.01
    return elevation, water


def drop_islets(elevation, water, w, h, sea_level):
    """Sink land specks (noise artifacts) too small to hold anything — kills the
    salt-and-pepper coastline."""
    seen = [[False] * w for _ in range(h)]
    for sy in range(h):
        for sx in range(w):
            if not water[sy][sx] and not seen[sy][sx]:
                cells = []
                q = deque([(sx, sy)])
                seen[sy][sx] = True
                while q:
                    x, y = q.popleft()
                    cells.append((x, y))
                    for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                        nx, ny = x + dx, y + dy
                        if 0 <= nx < w and 0 <= ny < h and not water[ny][nx] and not seen[ny][nx]:
                            seen[ny][nx] = True
                            q.append((nx, ny))
                if len(cells) <= _ISLET_MAX_CELLS:
                    for x, y in cells:
                        water[y][x] = True
                        elevation[y][x] = sea_level - 0.01
    return elevation, water
