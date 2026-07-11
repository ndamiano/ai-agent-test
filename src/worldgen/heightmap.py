from . import noise


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
    return _normalize(combined, w, h)
