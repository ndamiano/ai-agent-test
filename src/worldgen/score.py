from .context import WorldCtx
from .moisture import split_palette
from .wants import score_candidate


def _road_connectivity(world):
    sites = [s for s in world["sites"] if s["type"] == "settlement"]
    if len(sites) < 2:
        return 1.0

    parent = {s["id"]: s["id"] for s in sites}

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    coord_to_id = {(s["x"], s["y"]): s["id"] for s in sites}
    for path in world["roads"]:
        endpoints = [coord_to_id[tuple(c)] for c in (path[0], path[-1]) if tuple(c) in coord_to_id]
        if len(endpoints) == 2:
            union(endpoints[0], endpoints[1])

    roots = {find(s["id"]) for s in sites}
    largest = max(
        sum(1 for s in sites if find(s["id"]) == r)
        for r in roots
    )
    return largest / len(sites)


def _manifest_fit(world, recipe_doc, ctx):
    wants = {loc["id"]: loc.get("want") for loc in recipe_doc.get("locations", []) if loc.get("want")}
    if not wants:
        return 1.0

    by_id = {s["id"]: s for s in world["sites"]}
    scores = []
    for site_id, want in wants.items():
        site = by_id.get(site_id)
        if site is None or site.get("x") is None:
            continue
        others = [s for s in world["sites"] if s["id"] != site_id and s.get("x") is not None]
        s = score_candidate(site["x"], site["y"], want, ctx, others)
        scores.append(max(0.0, min(s if s is not None else 0.0, 2.0)) / 2.0)

    return sum(scores) / len(scores) if scores else 1.0


def _biome_variety(world, ctx):
    if not ctx.land_biomes:
        return 1.0
    present = {b for row, wrow in zip(world["biome"], world["water"])
               for b, is_water in zip(row, wrow) if not is_water}
    present_land = present & set(ctx.land_biomes)
    return len(present_land) / len(ctx.land_biomes)


def score(world: dict, recipe_doc: dict) -> float:
    """Manifest-fit quality: want-hint satisfaction, road connectivity, biome variety."""
    w, h = world["size"]["w"], world["size"]["h"]
    land_biomes, water_biomes = split_palette(recipe_doc["palette"]["biomes"])
    ctx = WorldCtx(w, h, world["elevation"], world["water"], world["biome"], land_biomes, water_biomes)

    fit = _manifest_fit(world, recipe_doc, ctx)
    roads_score = _road_connectivity(world)
    variety = _biome_variety(world, ctx)

    return 0.5 * fit + 0.3 * roads_score + 0.2 * variety
