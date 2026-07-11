"""Continuous-field re-detail: sample a macro-world window at k x resolution.

The macro grids (elevation, moisture) come from continuous fBm fields, so a zone can be
rendered at any resolution by evaluating them at fractional coordinates and adding
higher-frequency detail octaves. Biomes are re-classified at the fine resolution with the
same geography rules as moisture.assign_biomes, but every band EDGE (coast width, water
depth breaks, highland cut, moisture bands) wobbled by fBm so borders read organic instead
of rigid. The water line is re-thresholded ONLY inside the macro coast band (a macro cell
touching the land/water boundary): coastline gains fractal detail while interior land can
never turn to water — anchors, roads, and connectivity stay macro-true.
"""

from collections import deque

from . import moisture, noise, water as water_mod

_NEIGHBORS8 = ((1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (1, -1), (-1, 1), (-1, -1))

_WOBBLE_SCALE = 3.0        # macro cells per wobble-noise feature
_WOBBLE_OCTAVES = 3
_COAST_WOBBLE = 1.8        # +- macro cells on the coast band edge
_DEPTH_WOBBLE = 2.5        # +- macro cells on each water depth break
_MOIST_AMP = 0.35
_FINE_ELEV_SCALE = 5.0     # fine cells per elevation-detail feature
_FINE_ELEV_AMP = 0.045
_SEED_OFFSET = 424242


def _bilinear(grid, gw, gh, fx, fy):
    fx = min(max(fx, 0.0), gw - 1)
    fy = min(max(fy, 0.0), gh - 1)
    x0, y0 = int(fx), int(fy)
    x1, y1 = min(x0 + 1, gw - 1), min(y0 + 1, gh - 1)
    tx, ty = fx - x0, fy - y0
    top = grid[y0][x0] * (1 - tx) + grid[y0][x1] * tx
    bot = grid[y1][x0] * (1 - tx) + grid[y1][x1] * tx
    return top * (1 - ty) + bot * ty


def _dist_from(w, h, sources):
    dist = [[-1] * w for _ in range(h)]
    dq = deque()
    for y in range(h):
        for x in range(w):
            if sources[y][x]:
                dist[y][x] = 0
                dq.append((x, y))
    while dq:
        x, y = dq.popleft()
        d = dist[y][x] + 1
        for dx, dy in _NEIGHBORS8[:4]:
            nx, ny = x + dx, y + dy
            if 0 <= nx < w and 0 <= ny < h and dist[ny][nx] == -1:
                dist[ny][nx] = d
                dq.append((nx, ny))
    return dist


def sample(world, recipe, seed_base, wx0, wy0, ww0, wh0, k):
    """Resample window [wx0,wy0,ww0,wh0] at k x resolution (k >= 1 int).
    Returns (theme_grid, water_grid, elev_grid) at (ww0*k, wh0*k)."""
    W, H = world["size"]["w"], world["size"]["h"]
    elevation, wmask = world["elevation"], world["water"]
    palette_biomes = recipe["palette"]["biomes"]
    sea = water_mod.sea_level_for(elevation, W, H, recipe["archetype"])
    moist = moisture.build(W, H, seed_base)
    fw, fh = ww0 * k, wh0 * k

    def macro_at(x, y):
        return (min(wx0 + x // k, W - 1), min(wy0 + y // k, H - 1))

    coastband = [[False] * W for _ in range(H)]
    for y in range(H):
        for x in range(W):
            coastband[y][x] = any(
                0 <= x + dx < W and 0 <= y + dy < H and wmask[y + dy][x + dx] != wmask[y][x]
                for dx, dy in _NEIGHBORS8)

    elev_f = [[0.0] * fw for _ in range(fh)]
    water_f = [[False] * fw for _ in range(fh)]
    for y in range(fh):
        for x in range(fw):
            gx = wx0 + (x + 0.5) / k - 0.5
            gy = wy0 + (y + 0.5) / k - 0.5
            base = _bilinear(elevation, W, H, gx, gy)
            det = noise.fbm(x / _FINE_ELEV_SCALE, y / _FINE_ELEV_SCALE,
                            seed_base + _SEED_OFFSET + 31, octaves=4)
            e = base + det * _FINE_ELEV_AMP
            mx, my = macro_at(x, y)
            if coastband[my][mx]:
                w_here = e < sea
            else:
                w_here = wmask[my][mx]
                if w_here and e >= sea:
                    e = sea - 0.01
                elif not w_here and e < sea:
                    e = sea + 0.01
            elev_f[y][x] = min(max(e, 0.0), 1.0)
            water_f[y][x] = w_here

    land_biomes, water_biomes = moisture.split_palette(palette_biomes)
    water_biomes = sorted(water_biomes, key=moisture._depth_rank)
    coast = [b for b in land_biomes if moisture._has_keyword(b, moisture._COAST_KEYWORDS)]
    high = [b for b in land_biomes
            if b not in coast and moisture._has_keyword(b, moisture._HIGH_KEYWORDS)]
    mid = [b for b in land_biomes if b not in coast and b not in high] or land_biomes

    land_mask = [[not water_f[y][x] for x in range(fw)] for y in range(fh)]
    dist_from_land = _dist_from(fw, fh, land_mask)
    dist_from_water = _dist_from(fw, fh, water_f)

    land_elev = sorted(elev_f[y][x] for y in range(fh) for x in range(fw) if not water_f[y][x])
    hi_cut = (land_elev[int(moisture._HIGHLAND_QUANTILE * (len(land_elev) - 1))]
              if land_elev else sea + 1)

    def wob(x, y, salt):
        return noise.fbm(x / (_WOBBLE_SCALE * k), y / (_WOBBLE_SCALE * k),
                         seed_base + _SEED_OFFSET + salt, octaves=_WOBBLE_OCTAVES)

    theme_f = [[None] * fw for _ in range(fh)]
    for y in range(fh):
        for x in range(fw):
            if water_f[y][x]:
                if not water_biomes:
                    theme_f[y][x] = "sea"
                    continue
                d = dist_from_land[y][x] / k + wob(x, y, 0) * _DEPTH_WOBBLE
                band = 0
                for brk in moisture._WATER_DEPTH_BREAKS[:len(water_biomes) - 1]:
                    if d > brk:
                        band += 1
                theme_f[y][x] = water_biomes[band]
            elif coast and dist_from_water[y][x] / k <= (
                    moisture._COAST_LAND_DIST + wob(x, y, 7) * _COAST_WOBBLE):
                theme_f[y][x] = coast[0]
            elif high and elev_f[y][x] >= hi_cut:
                theme_f[y][x] = high[0]
            else:
                gx = wx0 + (x + 0.5) / k - 0.5
                gy = wy0 + (y + 0.5) / k - 0.5
                m = _bilinear(moist, W, H, gx, gy)
                m = min(1.0, max(0.0, m + wob(x, y, 19) * _MOIST_AMP))
                theme_f[y][x] = mid[moisture._band(m, 0.0, 1.0, len(mid))]

    return theme_f, water_f, elev_f


def sea_level(world, recipe):
    W, H = world["size"]["w"], world["size"]["h"]
    return water_mod.sea_level_for(world["elevation"], W, H, recipe["archetype"])
