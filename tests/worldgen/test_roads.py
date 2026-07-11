import worldgen


def _connected_components(sites, roads):
    coord_to_id = {(s["x"], s["y"]): s["id"] for s in sites}
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

    for path in roads:
        endpoints = [coord_to_id[tuple(c)] for c in (path[0], path[-1]) if tuple(c) in coord_to_id]
        if len(endpoints) == 2:
            union(*endpoints)

    return {find(s["id"]) for s in sites}


_CONTINENT_RECIPE = {
    "archetype": "continent",
    "size": "medium",
    "palette": {"biomes": ["ocean", "beach", "plains", "forest", "mountains"]},
    "locations": [
        {"id": "capital", "type": "settlement", "want": "coastal harbor"},
        {"id": "outpost", "type": "settlement", "want": "remote from capital"},
        {"id": "village", "type": "settlement", "want": "inland"},
    ],
}


def test_all_settlements_road_connected():
    world, _ = worldgen.generate_best(_CONTINENT_RECIPE, range(1, 30))
    settlements = [s for s in world["sites"] if s["type"] == "settlement"]
    assert len(settlements) == 3

    roots = _connected_components(settlements, world["roads"])
    assert len(roots) == 1


def test_single_settlement_has_no_roads(pirate_recipe):
    world = worldgen.generate(pirate_recipe, seed=1)
    settlements = [s for s in world["sites"] if s["type"] == "settlement"]
    assert len(settlements) == 1
    assert world["roads"] == []


def test_road_cells_are_land(pirate_recipe):
    world = worldgen.generate(pirate_recipe, seed=1)
    for path in world["roads"]:
        for x, y in path:
            assert not world["water"][y][x]
