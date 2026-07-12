import worldgen


def test_determinism(pirate_recipe):
    a = worldgen.generate(pirate_recipe, seed=7)
    b = worldgen.generate(pirate_recipe, seed=7)
    assert a["rivers"] == b["rivers"]
    assert a["lakes"] == b["lakes"]
    assert a["bridges"] == b["bridges"]


def test_river_paths_end_at_water(pirate_recipe):
    world = worldgen.generate(pirate_recipe, seed=7)
    water = world["water"]
    for path in world["rivers"]:
        assert len(path) >= 2
        last_x, last_y = path[-1]
        assert water[last_y][last_x]


def test_river_water_cells_are_accounted_for(pirate_recipe):
    """Rivers are an overlay: a path cell that reads as water in the mask must
    either be one of this world's authored lakes (a pit the river flooded
    mid-course) or the very last cell of the path (where it reaches the sea) —
    never an unexplained water cell, and never plain land silently flipped."""
    world = worldgen.generate(pirate_recipe, seed=7)
    water = world["water"]
    lake_cells = {tuple(c) for lake in world["lakes"] for c in lake}
    for path in world["rivers"]:
        for i, (x, y) in enumerate(path):
            if water[y][x]:
                assert (x, y) in lake_cells or i == len(path) - 1


def test_lakes_are_water(pirate_recipe):
    world = worldgen.generate(pirate_recipe, seed=7)
    water = world["water"]
    for lake in world["lakes"]:
        for x, y in lake:
            assert water[y][x]


def test_rivers_avoid_settlement_cells(pirate_recipe):
    world = worldgen.generate(pirate_recipe, seed=7)
    settlement_cells = {
        tuple(c) for s in world["sites"] if s["type"] == "settlement"
        for c in s.get("cells", [])
    }
    for path in world["rivers"]:
        for x, y in path:
            assert (x, y) not in settlement_cells


def test_bridges_lie_on_road_and_river(pirate_recipe):
    world = worldgen.generate(pirate_recipe, seed=7)
    road_cells = {tuple(c) for path in world["roads"] for c in path}
    river_cells = {tuple(c) for path in world["rivers"] for c in path}
    for bx, by in world["bridges"]:
        assert (bx, by) in road_cells
        assert (bx, by) in river_cells


def test_erosion_keeps_elevation_normalized_and_rectangular(pirate_recipe):
    world = worldgen.generate(pirate_recipe, seed=7)
    elevation = world["elevation"]
    w, h = world["size"]["w"], world["size"]["h"]
    assert len(elevation) == h
    assert all(len(row) == w for row in elevation)
    lo = min(min(row) for row in elevation)
    hi = max(max(row) for row in elevation)
    assert lo >= 0.0
    assert hi <= 1.0 + 1e-9
    assert hi > lo


def test_several_recipes_produce_at_least_one_river():
    """Not every seed guarantees a river (sources can all be blocked or every
    trace can be dropped), but across a batch on a reasonably sized continent
    at least one should land."""
    recipe_doc = {
        "archetype": "continent",
        "size": "medium",
        "palette": {"biomes": ["ocean", "beach", "plains", "forest", "mountains"]},
        "locations": [
            {"id": "capital", "type": "settlement", "want": "coastal harbor"},
        ],
    }
    found = False
    for seed in range(1, 15):
        world = worldgen.generate(recipe_doc, seed=seed)
        if world["rivers"]:
            found = True
            break
    assert found
