import random

from . import (
    heightmap,
    hydrology,
    manifest,
    moisture,
    poi,
    recipe,
    roads,
    settlements,
    water,
)
from .context import WorldCtx
from .errors import PlacementError
from .score import score


def generate(recipe_doc: dict, seed: int) -> dict:
    """Deterministic: same (recipe, seed) always produces the same world.
    Raises PlacementError if the manifest can't be satisfied (reroll the seed)."""
    recipe.validate(recipe_doc)
    w, h = recipe.dims(recipe_doc)
    archetype = recipe_doc["archetype"]
    rng = random.Random(seed)

    elevation = heightmap.build(w, h, seed, rng, archetype)
    sea_level = water.sea_level_for(elevation, w, h, archetype)
    water_mask = water.build_mask(elevation, w, h, sea_level)
    elevation, water_mask = water.fill_puddles(elevation, water_mask, w, h, sea_level)
    elevation, water_mask = water.drop_islets(elevation, water_mask, w, h, sea_level)

    moisture_grid = moisture.build(w, h, seed)
    land_biomes, water_biomes = moisture.split_palette(recipe_doc["palette"]["biomes"])
    palette_biomes = recipe_doc["palette"]["biomes"]
    biome = moisture.assign_biomes(elevation, water_mask, moisture_grid,
                                    palette_biomes, sea_level, w, h)

    # Settlements are placed before hydrology, reversing the design's natural
    # heightmap->hydrology->settlements order: settlements.py has no
    # river-awareness hook to own here, so instead the river tracer treats
    # already-placed settlement footprints as blocked terrain, guaranteeing a
    # river can never cross one.
    ctx = WorldCtx(w, h, elevation, water_mask, biome, land_biomes, water_biomes, seed=seed)
    locations = recipe_doc.get("locations", [])
    placed_settlements = settlements.place_settlements(locations, ctx)

    settlement_cells = {tuple(c) for s in placed_settlements for c in s.get("cells", [])}
    rivers, lakes, water_mask = hydrology.carve(elevation, water_mask, w, h, rng,
                                                 avoid_cells=settlement_cells)

    # Lakes just merged into the mask; a full biome recompute keeps water
    # biomes on exactly the water cells (the invariant `assign_biomes` already
    # guarantees for the sea) and gives lake shores their coast biome.
    biome = moisture.assign_biomes(elevation, water_mask, moisture_grid,
                                    palette_biomes, sea_level, w, h)
    ctx = WorldCtx(w, h, elevation, water_mask, biome, land_biomes, water_biomes, seed=seed)

    river_cells = {tuple(c) for path in rivers for c in path}
    road_paths, bridges = roads.connect(placed_settlements, ctx, river_cells)
    sites = manifest.place_remaining(locations, placed_settlements, ctx)

    world = {
        "size": {"w": w, "h": h},
        "elevation": elevation,
        "water": water_mask,
        "biome": biome,
        "sites": sites,
        "roads": road_paths,
        "rivers": rivers,
        "lakes": lakes,
        "bridges": bridges,
        "meta": {"seed": seed, "recipe_hash": recipe.recipe_hash(recipe_doc)},
    }
    world["sites"] = sites + poi.derive_pois(world, ctx)
    return world


def generate_best(recipe_doc: dict, seeds) -> tuple:
    """Generate over `seeds`, skipping ones that raise PlacementError.
    Returns (best_world, best_seed) by score(). Raises PlacementError if every seed fails."""
    best_world = None
    best_seed = None
    best_score = None
    last_error = None

    for seed in seeds:
        try:
            world = generate(recipe_doc, seed)
        except PlacementError as exc:
            last_error = exc
            continue
        s = score(world, recipe_doc)
        if best_score is None or s > best_score:
            best_score = s
            best_world = world
            best_seed = seed

    if best_world is None:
        raise last_error or PlacementError("no seed produced a placeable world")

    return best_world, best_seed
