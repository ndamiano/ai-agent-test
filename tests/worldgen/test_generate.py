import json
import time

import worldgen


def test_determinism(pirate_recipe):
    a = worldgen.generate(pirate_recipe, seed=42)
    b = worldgen.generate(pirate_recipe, seed=42)
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def test_different_seeds_differ(pirate_recipe):
    a = worldgen.generate(pirate_recipe, seed=1)
    b = worldgen.generate(pirate_recipe, seed=2)
    assert json.dumps(a, sort_keys=True) != json.dumps(b, sort_keys=True)


def test_medium_world_under_five_seconds(pirate_recipe):
    start = time.time()
    worldgen.generate(pirate_recipe, seed=1)
    assert time.time() - start < 5.0


def test_output_shape(pirate_recipe):
    world = worldgen.generate(pirate_recipe, seed=1)
    w, h = world["size"]["w"], world["size"]["h"]
    assert (w, h) == (128, 96)
    assert len(world["elevation"]) == h
    assert all(len(row) == w for row in world["elevation"])
    assert len(world["water"]) == h
    assert len(world["biome"]) == h
    assert world["meta"]["seed"] == 1
    assert world["meta"]["recipe_hash"]
