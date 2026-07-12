import worldgen
from worldgen import poi


def _world(pirate_recipe, seed=3):
    return worldgen.generate(pirate_recipe, seed=seed)


def _road_cells(world):
    cells = set()
    for path in world.get("roads", []):
        for x, y in path:
            cells.add((x, y))
    return cells


def _site_cells(world):
    cells = set()
    for site in world.get("sites", []):
        for c in site.get("cells") or []:
            cells.add((c[0], c[1]))
        cells.add((site["x"], site["y"]))
    return cells


def test_deterministic(pirate_recipe):
    world = _world(pirate_recipe)
    a = poi.derive_pois(world)
    b = poi.derive_pois(world)
    assert a == b


def test_pois_on_land(pirate_recipe):
    world = _world(pirate_recipe)
    pois = poi.derive_pois(world)
    assert pois
    for p in pois:
        assert not world["water"][p["y"]][p["x"]]


def test_no_overlap_with_roads_or_sites(pirate_recipe):
    world = _world(pirate_recipe)
    pois = poi.derive_pois(world)
    roads = _road_cells(world)
    sites = _site_cells(world)
    for p in pois:
        cell = (p["x"], p["y"])
        assert cell not in roads
        assert cell not in sites


def test_spacing_from_each_other_and_sites(pirate_recipe):
    world = _world(pirate_recipe)
    pois = poi.derive_pois(world)
    points = [(s["x"], s["y"]) for s in world["sites"]]
    for i, p in enumerate(pois):
        for q in pois[i + 1:]:
            assert abs(p["x"] - q["x"]) + abs(p["y"] - q["y"]) >= poi._MIN_SPACING
        for sx, sy in points:
            assert abs(p["x"] - sx) + abs(p["y"] - sy) >= poi._MIN_SPACING


def test_unique_ids_and_shape(pirate_recipe):
    world = _world(pirate_recipe)
    pois = poi.derive_pois(world)
    ids = [p["id"] for p in pois]
    assert len(ids) == len(set(ids))
    for p in pois:
        assert set(p.keys()) == {"id", "type", "x", "y", "derived"}
        assert p["type"] == "landmark"


def test_count_within_cap(pirate_recipe):
    world = _world(pirate_recipe)
    pois = poi.derive_pois(world)
    assert 0 <= len(pois) <= poi._MAX_POIS


def test_accepts_explicit_seed_and_matches_world_seed(pirate_recipe):
    world = _world(pirate_recipe)
    a = poi.derive_pois(world, world["meta"]["seed"])
    b = poi.derive_pois(world)
    assert a == b


def test_river_mouth_bonus_signature_when_present(pirate_recipe):
    world = _world(pirate_recipe)
    world = dict(world)
    w, h = world["size"]["w"], world["size"]["h"]
    water = world["water"]
    path = None
    for y in range(1, h - 1):
        for x in range(1, w - 1):
            if not water[y][x]:
                for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    if water[y + dy][x + dx]:
                        path = [[x, y], [x + dx, y + dy]]
                        break
            if path:
                break
        if path:
            break
    assert path is not None
    world["rivers"] = [path]
    pois = poi.derive_pois(world)
    for p in pois:
        assert not water[p["y"]][p["x"]]


def test_ignores_unknown_extra_keys(pirate_recipe):
    world = _world(pirate_recipe)
    world = dict(world)
    world["lakes"] = [{"cells": [[1, 1]]}]
    world["bridges"] = [{"x": 2, "y": 2}]
    pois = poi.derive_pois(world)
    assert isinstance(pois, list)


def test_does_not_mutate_world(pirate_recipe):
    world = _world(pirate_recipe)
    before_sites = len(world["sites"])
    poi.derive_pois(world)
    assert len(world["sites"]) == before_sites

