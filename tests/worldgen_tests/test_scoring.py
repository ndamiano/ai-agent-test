import statistics

import worldgen
from worldgen.wants import is_coastal


def test_remote_from_beats_median_candidate_distance(pirate_recipe):
    world = worldgen.generate(pirate_recipe, seed=1)
    by_id = {s["id"]: s for s in world["sites"]}
    home = by_id["home_port"]
    remote = by_id["maroon_beach"]

    w, h = world["size"]["w"], world["size"]["h"]
    water = world["water"]
    candidate_distances = [
        ((x - home["x"]) ** 2 + (y - home["y"]) ** 2) ** 0.5
        for y in range(h)
        for x in range(w)
        if is_coastal(x, y, water, w, h)
    ]
    median = statistics.median(candidate_distances)

    actual = ((remote["x"] - home["x"]) ** 2 + (remote["y"] - home["y"]) ** 2) ** 0.5
    assert actual > median


def test_generate_best_returns_higher_or_equal_score_than_any_single_seed(pirate_recipe):
    seeds = range(1, 11)
    best_world, best_seed = worldgen.generate_best(pirate_recipe, seeds)
    best_score = worldgen.score(best_world, pirate_recipe)

    for seed in seeds:
        try:
            world = worldgen.generate(pirate_recipe, seed)
        except worldgen.PlacementError:
            continue
        assert worldgen.score(world, pirate_recipe) <= best_score + 1e-9

    assert best_seed in seeds


def test_score_is_bounded(pirate_recipe):
    world = worldgen.generate(pirate_recipe, seed=1)
    s = worldgen.score(world, pirate_recipe)
    assert 0.0 <= s <= 1.0
