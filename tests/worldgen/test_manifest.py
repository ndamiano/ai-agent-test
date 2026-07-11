import worldgen


def test_manifest_fully_placed(pirate_recipe):
    world = worldgen.generate(pirate_recipe, seed=1)
    ids = {s["id"] for s in world["sites"]}
    expected = {loc["id"] for loc in pirate_recipe["locations"]}
    assert ids == expected


def test_interior_host_binding_recorded(pirate_recipe):
    world = worldgen.generate(pirate_recipe, seed=1)
    by_id = {s["id"]: s for s in world["sites"]}
    tavern = by_id["tavern"]
    assert tavern["host"] == "home_port"
    assert tavern["x"] == by_id["home_port"]["x"]
    assert tavern["y"] == by_id["home_port"]["y"]
    assert tavern["cells"] == []


def test_open_water_site_is_water_cells(pirate_recipe):
    world = worldgen.generate(pirate_recipe, seed=1)
    by_id = {s["id"]: s for s in world["sites"]}
    sea = by_id["sea"]
    assert sea["cells"]
    for x, y in sea["cells"]:
        assert world["water"][y][x]


def test_wilderness_and_coastal_strip_are_land_cells(pirate_recipe):
    world = worldgen.generate(pirate_recipe, seed=1)
    by_id = {s["id"]: s for s in world["sites"]}
    for site_id in ("wilds", "maroon_beach", "home_port"):
        site = by_id[site_id]
        for x, y in site["cells"]:
            assert not world["water"][y][x]
