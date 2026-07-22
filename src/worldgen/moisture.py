from collections import deque

from . import noise

_WATER_KEYWORDS = ("shallow", "shallows", "reef", "ocean", "lagoon", "tide", "tidal", "lake", "water", "sea")
_MOISTURE_SEED_OFFSET = 91013


def is_water_biome(name):
    lname = name.lower()
    return any(k in lname for k in _WATER_KEYWORDS)


def split_palette(biomes):
    water_biomes = [b for b in biomes if is_water_biome(b)]
    land_biomes = [b for b in biomes if not is_water_biome(b)]
    if not land_biomes:
        land_biomes = list(biomes)
    return land_biomes, water_biomes


def build(w, h, seed):
    scale = max(w, h) / 5.0
    grid = [[0.0] * w for _ in range(h)]
    for y in range(h):
        for x in range(w):
            v = noise.fbm(x / scale, y / scale, seed + _MOISTURE_SEED_OFFSET, octaves=4)
            grid[y][x] = (v + 1) / 2
    return grid


def _band(value, lo, hi, n):
    if hi <= lo:
        return 0
    frac = (value - lo) / (hi - lo)
    band = int(frac * n)
    return max(0, min(n - 1, band))


_COAST_KEYWORDS = ("beach", "sand", "shore", "coast", "dune")
_HIGH_KEYWORDS = ("highland", "mountain", "rock", "hill", "peak", "cliff", "crag", "volcan")
_DEPTH_RANK = (("shallow", 0), ("lagoon", 0), ("tide", 0), ("reef", 1),
               ("deep", 3), ("abyss", 3), ("open", 2), ("ocean", 2), ("sea", 2))
_WATER_DEPTH_BREAKS = (3, 7, 12, 18)
_COAST_LAND_DIST = 2
_HIGHLAND_QUANTILE = 0.8


def _depth_rank(name):
    lname = name.lower()
    for kw, rank in _DEPTH_RANK:
        if kw in lname:
            return rank
    return 1


def _has_keyword(name, keywords):
    lname = name.lower()
    return any(k in lname for k in keywords)


def _dist_from(w, h, sources_mask):
    dist = [[-1] * w for _ in range(h)]
    q = deque()
    for y in range(h):
        for x in range(w):
            if sources_mask[y][x]:
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


def assign_biomes(elevation, water, moisture, palette_biomes, sea_level, w, h):
    """Geography decides the biome, keywords map the palette's labels onto roles:
    water bands by distance from land (shallow -> deep), coast-named biomes take the
    shoreline fringe, high-named biomes take the top elevation band, the rest band
    by moisture."""
    land_biomes, water_biomes = split_palette(palette_biomes)
    water_biomes = sorted(water_biomes, key=_depth_rank)

    coast = [b for b in land_biomes if _has_keyword(b, _COAST_KEYWORDS)]
    high = [b for b in land_biomes if b not in coast and _has_keyword(b, _HIGH_KEYWORDS)]
    mid = [b for b in land_biomes if b not in coast and b not in high]
    if not mid:
        mid = land_biomes

    land_mask = [[not water[y][x] for x in range(w)] for y in range(h)]
    dist_from_land = _dist_from(w, h, land_mask)
    dist_from_water = _dist_from(w, h, water)

    land_elevations = sorted(elevation[y][x] for y in range(h) for x in range(w) if not water[y][x])
    hi_cut = (land_elevations[int(_HIGHLAND_QUANTILE * (len(land_elevations) - 1))]
              if land_elevations else sea_level + 1)

    biome = [[None] * w for _ in range(h)]
    for y in range(h):
        for x in range(w):
            if water[y][x]:
                if not water_biomes:
                    biome[y][x] = "sea"
                    continue
                d = dist_from_land[y][x]
                band = 0
                for brk in _WATER_DEPTH_BREAKS[:len(water_biomes) - 1]:
                    if d > brk:
                        band += 1
                biome[y][x] = water_biomes[band]
            elif coast and dist_from_water[y][x] <= _COAST_LAND_DIST:
                biome[y][x] = coast[0]
            elif high and elevation[y][x] >= hi_cut:
                biome[y][x] = high[0]
            else:
                biome[y][x] = mid[_band(moisture[y][x], 0.0, 1.0, len(mid))]
    return biome
