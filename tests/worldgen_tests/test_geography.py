"""The fixes real use forced: island-scoped roads, geography-role biomes, border margins."""
import pytest

from worldgen import generate_best
from worldgen.moisture import is_water_biome

MULTI_PORT_RECIPE = {
    "archetype": "archipelago", "size": "small",
    "palette": {"biomes": ["open sea", "reef", "tropical shallows",
                           "beach", "jungle", "rocky highlands"]},
    "locations": [
        {"id": "home_port", "type": "settlement", "want": "coastal harbor"},
        {"id": "smugglers_port", "type": "settlement", "want": "coastal, remote from home_port"},
        {"id": "naval_port", "type": "settlement", "want": "coastal harbor"},
        {"id": "tavern", "type": "interior", "host": "home_port"},
        {"id": "maroon_beach", "type": "coastal_strip", "want": "remote from home_port"},
        {"id": "wilds", "type": "wilderness", "want": "jungle inland"},
        {"id": "sea", "type": "open_water", "want": "large"},
    ],
}


@pytest.fixture(scope="module")
def multi_port_world():
    world, _ = generate_best(MULTI_PORT_RECIPE, range(1, 21))
    return world


def test_archipelago_multi_settlement_generates(multi_port_world):
    placed = {s["id"] for s in multi_port_world["sites"]}
    expected = {loc["id"] for loc in MULTI_PORT_RECIPE["locations"]}
    assert expected <= placed
    assert all(sid.startswith("poi_") for sid in placed - expected)


def test_roads_never_cross_water(multi_port_world):
    water = multi_port_world["water"]
    for path in multi_port_world["roads"]:
        assert all(not water[y][x] for x, y in path)


def test_water_biomes_only_on_water(multi_port_world):
    water, biome = multi_port_world["water"], multi_port_world["biome"]
    w, h = multi_port_world["size"]["w"], multi_port_world["size"]["h"]
    for y in range(h):
        for x in range(w):
            assert is_water_biome(biome[y][x]) == water[y][x]


def test_water_depth_bands_shallow_at_coast(multi_port_world):
    water, biome = multi_port_world["water"], multi_port_world["biome"]
    w, h = multi_port_world["size"]["w"], multi_port_world["size"]["h"]
    coastal_water = set()
    for y in range(h):
        for x in range(w):
            if not water[y][x]:
                continue
            if any(0 <= x + dx < w and 0 <= y + dy < h and not water[y + dy][x + dx]
                   for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1))):
                coastal_water.add(biome[y][x])
    assert coastal_water == {"tropical shallows"}


def test_coast_land_is_beach(multi_port_world):
    water, biome = multi_port_world["water"], multi_port_world["biome"]
    w, h = multi_port_world["size"]["w"], multi_port_world["size"]["h"]
    for y in range(h):
        for x in range(w):
            if water[y][x]:
                continue
            if any(0 <= x + dx < w and 0 <= y + dy < h and water[y + dy][x + dx]
                   for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1))):
                assert biome[y][x] == "beach"


def test_sites_off_borders(multi_port_world):
    w, h = multi_port_world["size"]["w"], multi_port_world["size"]["h"]
    for site in multi_port_world["sites"]:
        assert 3 <= site["x"] < w - 3
        assert 3 <= site["y"] < h - 3


FAIRY_RECIPE = {
    "archetype": "continent", "size": "small",
    "palette": {"biomes": ["glimmerpond", "enchanted meadow", "mushroom grove",
                           "gloomwood", "sugarplum hills"]},
    "locations": [
        {"id": "fairy_village", "type": "settlement", "want": "enchanted meadow"},
        {"id": "toadstool_cottage", "type": "interior", "host": "fairy_village"},
        {"id": "mushroom_grove", "type": "wilderness",
         "want": "mushroom grove, remote from fairy_village"},
        {"id": "gloomwood", "type": "wilderness", "want": "gloomwood, remote from fairy_village"},
        {"id": "chin_ridge", "type": "landmark", "want": "sugarplum hills, remote from fairy_village"},
        {"id": "chin_pond", "type": "landmark", "want": "near fairy_village"},
        {"id": "chin_meadow", "type": "landmark", "want": "enchanted meadow, remote from gloomwood"},
    ],
}


@pytest.fixture(scope="module")
def fairy_world():
    world, _ = generate_best(FAIRY_RECIPE, range(1, 21))
    return world


def test_fairy_theme_generates(fairy_world):
    placed = {s["id"] for s in fairy_world["sites"]}
    expected = {loc["id"] for loc in FAIRY_RECIPE["locations"]}
    assert expected <= placed
    assert all(sid.startswith("poi_") for sid in placed - expected)


def test_no_water_biome_palette_falls_back(fairy_world):
    water, biome = fairy_world["water"], fairy_world["biome"]
    w, h = fairy_world["size"]["w"], fairy_world["size"]["h"]
    assert all(biome[y][x] == "sea" for y in range(h) for x in range(w) if water[y][x])


def test_sites_never_stack(fairy_world):
    located = [s for s in fairy_world["sites"] if s["type"] != "interior"]
    for i, a in enumerate(located):
        for b in located[i + 1:]:
            assert abs(a["x"] - b["x"]) + abs(a["y"] - b["y"]) >= 6, (a["id"], b["id"])


def test_biome_wants_honored(fairy_world):
    biome = fairy_world["biome"]
    sites = {s["id"]: s for s in fairy_world["sites"]}
    for sid, wanted in (("mushroom_grove", "mushroom grove"),
                        ("gloomwood", "gloomwood"),
                        ("chin_ridge", "sugarplum hills")):
        s = sites[sid]
        assert biome[s["y"]][s["x"]] == wanted
