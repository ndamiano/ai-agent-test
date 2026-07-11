import math

from . import noise

_D8 = ((1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (1, -1), (-1, 1), (-1, -1))
_EROSION_PASSES = 2
_EROSION_FACTOR = 0.025


def _flow_accumulation(grid, w, h):
    """Steepest-descent flow direction per cell, then accumulate downstream in
    descending-elevation order (a valid topological order: a cell only feeds a
    strictly lower neighbor, so every upstream contributor is processed first)."""
    cells = sorted(((y, x) for y in range(h) for x in range(w)), key=lambda yx: -grid[yx[0]][yx[1]])
    downhill = {}
    for y, x in cells:
        target = None
        lowest = grid[y][x]
        for dx, dy in _D8:
            nx, ny = x + dx, y + dy
            if 0 <= nx < w and 0 <= ny < h and grid[ny][nx] < lowest:
                lowest = grid[ny][nx]
                target = (nx, ny)
        downhill[(x, y)] = target

    accum = [[1.0] * w for _ in range(h)]
    for y, x in cells:
        target = downhill[(x, y)]
        if target is not None:
            tx, ty = target
            accum[ty][tx] += accum[y][x]
    return accum


def _erode(grid, w, h):
    """Cheap flow-based carve: lower each cell proportional to how much of the
    map drains through it, so valleys and drainage patterns emerge without a
    full hydraulic sim."""
    eroded = [row[:] for row in grid]
    for _ in range(_EROSION_PASSES):
        accum = _flow_accumulation(eroded, w, h)
        eroded = [
            [eroded[y][x] - _EROSION_FACTOR * math.log(1 + accum[y][x]) for x in range(w)]
            for y in range(h)
        ]
    return eroded


def _base_noise(w, h, seed):
    scale = max(w, h) / 6.0
    grid = [[0.0] * w for _ in range(h)]
    for y in range(h):
        for x in range(w):
            grid[y][x] = noise.fbm(x / scale, y / scale, seed, octaves=5)
    return grid


def _archipelago_mass(w, h, rng):
    num_islands = rng.randint(4, 7)
    islands = []
    for _ in range(num_islands):
        cx = rng.uniform(0.15, 0.85) * w
        cy = rng.uniform(0.15, 0.85) * h
        radius = rng.uniform(0.12, 0.22) * min(w, h)
        islands.append((cx, cy, radius))

    mass = [[0.0] * w for _ in range(h)]
    for y in range(h):
        for x in range(w):
            best = 0.0
            for cx, cy, radius in islands:
                d = ((x - cx) ** 2 + (y - cy) ** 2) ** 0.5
                falloff = max(0.0, 1.0 - d / radius) ** 1.5
                if falloff > best:
                    best = falloff
            mass[y][x] = best
    return mass


def _continent_mass(w, h, rng):
    cx = w / 2 + rng.uniform(-0.05, 0.05) * w
    cy = h / 2 + rng.uniform(-0.05, 0.05) * h
    radius = 0.42 * min(w, h)
    mass = [[0.0] * w for _ in range(h)]
    for y in range(h):
        for x in range(w):
            d = ((x - cx) ** 2 + (y - cy) ** 2) ** 0.5
            mass[y][x] = max(0.0, 1.0 - d / radius)
    return mass


def _normalize(grid, w, h):
    lo = min(min(row) for row in grid)
    hi = max(max(row) for row in grid)
    span = hi - lo or 1.0
    return [[(grid[y][x] - lo) / span for x in range(w)] for y in range(h)]


def build(w, h, seed, rng, archetype):
    base = _base_noise(w, h, seed)
    mass = _archipelago_mass(w, h, rng) if archetype == "archipelago" else _continent_mass(w, h, rng)

    blend = 0.5 if archetype == "archipelago" else 0.35
    combined = [
        [base[y][x] * blend + mass[y][x] * (1 - blend) for x in range(w)]
        for y in range(h)
    ]
    eroded = _erode(combined, w, h)
    return _normalize(eroded, w, h)
