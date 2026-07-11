import worldgen
from worldgen import detail
from worldgen.zones import build_zones

_SEEDS = range(1, 21)


def _world(pirate_recipe):
    return worldgen.generate_best(pirate_recipe, _SEEDS)[0]


def test_sample_dims_and_determinism(pirate_recipe):
    world = _world(pirate_recipe)
    seed = world["meta"]["seed"]
    a = detail.sample(world, pirate_recipe, seed, 10, 10, 20, 15, 2)
    b = detail.sample(world, pirate_recipe, seed, 10, 10, 20, 15, 2)
    for ga, gb in zip(a, b):
        assert ga == gb
    theme, water, elev = a
    assert len(theme) == 30 and len(theme[0]) == 40
    assert len(water) == 30 and len(elev) == 30


def test_water_flips_only_in_coast_band(pirate_recipe):
    world = _world(pirate_recipe)
    seed = world["meta"]["seed"]
    W, H = world["size"]["w"], world["size"]["h"]
    wmask = world["water"]
    k = 3
    _, water_f, _ = detail.sample(world, pirate_recipe, seed, 0, 0, W, H, k)

    def near_boundary(mx, my):
        return any(0 <= mx + dx < W and 0 <= my + dy < H
                   and wmask[my + dy][mx + dx] != wmask[my][mx]
                   for dx in (-1, 0, 1) for dy in (-1, 0, 1))

    for y in range(H * k):
        for x in range(W * k):
            mx, my = x // k, y // k
            if not near_boundary(mx, my):
                assert water_f[y][x] == wmask[my][mx]


def test_elevation_normalized(pirate_recipe):
    world = _world(pirate_recipe)
    seed = world["meta"]["seed"]
    _, _, elev = detail.sample(world, pirate_recipe, seed, 0, 0, 32, 24, 2)
    assert all(0.0 <= v <= 1.0 for row in elev for v in row)


def test_zone_upsample_scale_declared(pirate_recipe):
    world = _world(pirate_recipe)
    places = build_zones(world, pirate_recipe)["places"]
    kinds = {p["kind"] for p in places.values()}
    assert {"world_map", "town", "interior"} <= kinds
    for p in places.values():
        assert p["m_per_cell"] > 0
    towns = [p for p in places.values() if p["kind"] == "town"]
    landmasses = [p for p in places.values()
                  if p["kind"] == "world_map" and any(
                      s["theme"] != "worn path" and s["role"] == "open"
                      for s in p["tiles"]["legend"].values())]
    assert all(t["m_per_cell"] < lm["m_per_cell"] for t in towns for lm in landmasses)


def test_landmass_zone_larger_than_macro_window(pirate_recipe):
    world = _world(pirate_recipe)
    places = build_zones(world, pirate_recipe)["places"]
    for p in places.values():
        if p["kind"] != "world_map" or "layout" not in p:
            continue
        wx0, wy0, ww0, wh0 = p["layout"]["window"]
        rows = p["tiles"]["rows"]
        if p["m_per_cell"] < 60.0:
            assert len(rows[0]) == ww0 * round(60.0 / p["m_per_cell"])
            assert len(rows) == wh0 * round(60.0 / p["m_per_cell"])
        assert "sea_level" in p
        assert len(p["elevation"]) == len(rows)
        assert len(p["elevation"][0]) == len(rows[0])
