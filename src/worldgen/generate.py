import random

from . import heightmap, manifest, moisture, recipe, roads, settlements, water
from .context import WorldCtx


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
    biome = moisture.assign_biomes(elevation, water_mask, moisture_grid,
                                    recipe_doc["palette"]["biomes"], sea_level, w, h)

    ctx = WorldCtx(w, h, elevation, water_mask, biome, land_biomes, water_biomes, seed=seed)

    locations = recipe_doc.get("locations", [])
    placed_settlements = settlements.place_settlements(locations, ctx)
    road_paths = roads.connect(placed_settlements, ctx)
    sites = manifest.place_remaining(locations, placed_settlements, ctx)

    return {
        "size": {"w": w, "h": h},
        "elevation": elevation,
        "water": water_mask,
        "biome": biome,
        "sites": sites,
        "roads": road_paths,
        "meta": {"seed": seed, "recipe_hash": recipe.recipe_hash(recipe_doc)},
    }


def generate_best(recipe_doc: dict, seeds) -> tuple:
    """Generate over `seeds`, skipping ones that raise PlacementError.
    Returns (best_world, best_seed) by score(). Raises PlacementError if every seed fails."""
    from .errors import PlacementError
    from .score import score

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
