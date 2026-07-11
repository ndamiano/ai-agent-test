def _water_neighbor_fraction(x, y, water, w, h, radius):
    count = 0
    total = 0
    for dy in range(-radius, radius + 1):
        for dx in range(-radius, radius + 1):
            nx, ny = x + dx, y + dy
            if 0 <= nx < w and 0 <= ny < h:
                total += 1
                if water[ny][nx]:
                    count += 1
    return count / total if total else 0.0


def _local_elevation_variance(x, y, elevation, w, h, radius=1):
    vals = []
    for dy in range(-radius, radius + 1):
        for dx in range(-radius, radius + 1):
            nx, ny = x + dx, y + dy
            if 0 <= nx < w and 0 <= ny < h:
                vals.append(elevation[ny][nx])
    mean = sum(vals) / len(vals)
    return sum((v - mean) ** 2 for v in vals) / len(vals)


def is_coastal(x, y, water, w, h):
    return not water[y][x] and _water_neighbor_fraction(x, y, water, w, h, 1) > 0


_BORDER_MARGIN = 3
_REMOTE_CAP = 0.6
_SITE_SEPARATION = 6


def _jitter(x, y, seed):
    """Deterministic sub-milliscore noise so equal-score candidates don't all
    tie-break to the top-left corner."""
    return (((x * 73856093) ^ (y * 19349663) ^ (seed * 83492791)) % 9973) / 1e7


def score_candidate(x, y, want, ctx, placed_sites):
    """Score a candidate cell against a free-text `want` hint. Higher is better.
    Returns None if the want disqualifies the cell outright (e.g. not coastal)."""
    if (x < _BORDER_MARGIN or y < _BORDER_MARGIN
            or x >= ctx.w - _BORDER_MARGIN or y >= ctx.h - _BORDER_MARGIN):
        return None
    for site in placed_sites:
        if site.get("x") is None:
            continue
        if abs(x - site["x"]) + abs(y - site["y"]) < _SITE_SEPARATION:
            return None
    want_l = (want or "").lower()
    score = _jitter(x, y, ctx.seed)

    wants_coastal = "coastal" in want_l or "harbor" in want_l
    if wants_coastal:
        if ctx.water[y][x] or _water_neighbor_fraction(x, y, ctx.water, ctx.w, ctx.h, 1) <= 0:
            return None
        score += min(_water_neighbor_fraction(x, y, ctx.water, ctx.w, ctx.h, 1), 0.6)

    if "harbor" in want_l:
        score += _water_neighbor_fraction(x, y, ctx.water, ctx.w, ctx.h, 2)
        variance = _local_elevation_variance(x, y, ctx.elevation, ctx.w, ctx.h)
        score += max(0.0, 0.05 - variance) * 4

    if "inland" in want_l:
        if ctx.water[y][x]:
            return None
        score += min(ctx.water_dist[y][x] / max(1.0, (ctx.w + ctx.h) / 4), 1.0)

    if "large" in want_l:
        cid = ctx.component_id[y][x]
        size = ctx.component_size.get(cid, 0)
        score += min(size / (ctx.w * ctx.h), 1.0)

    for biome_name in ctx.land_biomes + ctx.water_biomes:
        if biome_name.lower() in want_l and ctx.biome[y][x] == biome_name:
            score += 1.0

    for site in placed_sites:
        if site.get("x") is None:
            continue
        d = ((x - site["x"]) ** 2 + (y - site["y"]) ** 2) ** 0.5 / ctx.diag
        if f"remote from {site['id']}".lower() in want_l:
            score += min(d, _REMOTE_CAP)
        if f"near {site['id']}".lower() in want_l:
            score += (1.0 - d)

    return score
