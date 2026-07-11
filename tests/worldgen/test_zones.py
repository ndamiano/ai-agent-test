import copy
from collections import deque

import pytest

import worldgen
from worldgen.zones import build_zones, render_zone

from maestro.modules.world import v_places

_PIRATE_RECIPE = {
    "archetype": "archipelago",
    "size": "small",
    "palette": {"biomes": ["open sea", "reef", "tropical shallows", "beach", "jungle",
                            "rocky highlands"]},
    "locations": [
        {"id": "home_port", "type": "settlement", "want": "coastal harbor"},
        {"id": "smugglers_port", "type": "settlement", "want": "coastal, remote from home_port"},
        {"id": "tavern", "type": "interior", "host": "home_port"},
        {"id": "maroon_beach", "type": "coastal_strip", "want": "remote from home_port"},
        {"id": "wilds", "type": "wilderness", "want": "jungle inland"},
        {"id": "sea", "type": "open_water", "want": "large"},
    ],
}

_SEEDS = range(1, 21)


@pytest.fixture
def pirate_recipe():
    return copy.deepcopy(_PIRATE_RECIPE)


@pytest.fixture
def pirate_zones(pirate_recipe):
    world, _seed = worldgen.generate_best(pirate_recipe, _SEEDS)
    places = build_zones(world, pirate_recipe)
    return world, places


def _open_cells(place):
    legend = place["tiles"]["legend"]
    rows = place["tiles"]["rows"]
    return {(x, y) for y, row in enumerate(rows) for x, ch in enumerate(row)
            if legend[ch]["role"] == "open"}


def _land_component_ids(world):
    """Independent landmass labeling (connected land-only BFS), so tests verify the CONTRACT
    ("sites on one landmass share a place") rather than re-running production's own grouping."""
    W, H = world["size"]["w"], world["size"]["h"]
    water = world["water"]
    comp = [[-1] * W for _ in range(H)]
    cid = 0
    for sy in range(H):
        for sx in range(W):
            if water[sy][sx] or comp[sy][sx] != -1:
                continue
            dq = deque([(sx, sy)])
            comp[sy][sx] = cid
            while dq:
                x, y = dq.popleft()
                for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    nx, ny = x + dx, y + dy
                    if (0 <= nx < W and 0 <= ny < H and not water[ny][nx]
                            and comp[ny][nx] == -1):
                        comp[ny][nx] = cid
                        dq.append((nx, ny))
            cid += 1
    return comp


def _marker_place(places, site_id):
    marker_id = f"h_{site_id}_marker"
    found = [pid for pid, place in places["places"].items()
             if any(h["id"] == marker_id for h in place["interactables"])]
    assert len(found) == 1, (site_id, found)
    return found[0]


def test_v_places_accepts_generated_world(pirate_zones):
    _world, places = pirate_zones
    assert v_places(places) is None


def test_rows_are_rectangular(pirate_zones):
    _world, places = pirate_zones
    for pid, place in places["places"].items():
        rows = place["tiles"]["rows"]
        assert rows, pid
        w = len(rows[0])
        assert all(len(r) == w for r in rows), pid


def test_interactables_on_open_unique_in_bounds_cells(pirate_zones):
    _world, places = pirate_zones
    for pid, place in places["places"].items():
        rows = place["tiles"]["rows"]
        w, h = len(rows[0]), len(rows)
        open_cells = _open_cells(place)
        seen = set()
        for hotspot in place["interactables"]:
            cell = hotspot["position"]["cell"]
            xy = (cell["x"], cell["y"])
            assert 0 <= xy[0] < w and 0 <= xy[1] < h, (pid, hotspot["id"])
            assert xy in open_cells, (pid, hotspot["id"], xy)
            assert xy not in seen, (pid, hotspot["id"], xy)
            seen.add(xy)


def test_move_targets_and_spawns_resolve(pirate_zones):
    _world, places = pirate_zones
    place_map = places["places"]
    for pid, place in place_map.items():
        for hotspot in place["interactables"]:
            action = hotspot["action"]
            if action["type"] != "move":
                continue
            target = action["target"]
            assert target in place_map, (pid, hotspot["id"], target)
            spawn = action["spawn"]["cell"]
            target_place = place_map[target]
            rows = target_place["tiles"]["rows"]
            w, h = len(rows[0]), len(rows)
            assert 0 <= spawn["x"] < w and 0 <= spawn["y"] < h, (pid, hotspot["id"])
            assert (spawn["x"], spawn["y"]) in _open_cells(target_place), (pid, hotspot["id"])


def test_zone_graph_fully_connected_from_start(pirate_zones):
    _world, places = pirate_zones
    place_map = places["places"]
    start = places["start_place"]
    assert start in place_map

    edges = {pid: [] for pid in place_map}
    for pid, place in place_map.items():
        for hotspot in place["interactables"]:
            action = hotspot["action"]
            if action["type"] == "move":
                edges[pid].append(action["target"])

    seen = {start}
    dq = deque([start])
    while dq:
        cur = dq.popleft()
        for nxt in edges[cur]:
            if nxt not in seen:
                seen.add(nxt)
                dq.append(nxt)

    assert seen == set(places["place_ids"])


def test_start_spawn_is_open_in_start_place(pirate_zones):
    _world, places = pirate_zones
    start_place = places["places"][places["start_place"]]
    cell = places["start_spawn"]["cell"]
    assert (cell["x"], cell["y"]) in _open_cells(start_place)


def test_render_zone_reports_each_place_id_and_dims(pirate_zones):
    _world, places = pirate_zones
    for pid, place in places["places"].items():
        text = render_zone(place)
        rows = place["tiles"]["rows"]
        dims = f"{len(rows[0])}x{len(rows)}"
        assert dims in text, pid
        assert place["kind"] in text, pid


def test_open_water_zone_inverts_walkability(pirate_zones):
    world, places = pirate_zones
    sea_site = next(s for s in world["sites"] if s["type"] == "open_water")
    sea_place = places["places"][sea_site["id"]]
    legend = sea_place["tiles"]["legend"]
    # the open_water site's own marker must be open (it is a water cell on the ocean map)
    hotspot = next(h for h in sea_place["interactables"] if h["id"].endswith("_marker"))
    cell = hotspot["position"]["cell"]
    assert legend[sea_place["tiles"]["rows"][cell["y"]][cell["x"]]]["role"] == "open"


def test_interior_place_links_back_to_host(pirate_zones):
    _world, places = pirate_zones
    tavern = places["places"]["tavern"]
    assert tavern["kind"] == "interior"
    move_out = [h for h in tavern["interactables"] if h["action"]["type"] == "move"]
    assert any(h["action"]["target"] == "home_port" for h in move_out)
    home_port = places["places"]["home_port"]
    assert any(h["action"].get("target") == "tavern" for h in home_port["interactables"])


def test_located_site_markers_on_landmass_or_ocean_place(pirate_zones):
    """No arbitrary per-site crops: every located site's marker (a settlement's enter hotspot,
    any other site's examine marker) lives on the ONE place for its landmass (or the ocean
    place, if it sits on water), never a bespoke window of its own."""
    world, places = pirate_zones
    water = world["water"]
    land_comp = _land_component_ids(world)
    landmass_place = {}
    for site in world["sites"]:
        if site.get("host"):
            continue
        if site["type"] == "settlement":
            hotspot_id = f"h_enter_{site['id']}"
            found = [pid for pid, place in places["places"].items()
                     if any(h["id"] == hotspot_id for h in place["interactables"])]
            assert len(found) == 1, (site["id"], found)
            pid = found[0]
        else:
            pid = _marker_place(places, site["id"])
        if water[site["y"]][site["x"]]:
            continue
        cid = land_comp[site["y"]][site["x"]]
        prev = landmass_place.get(cid)
        if prev is None:
            landmass_place[cid] = pid
        else:
            assert prev == pid, (site["id"], cid, prev, pid)


def test_zone_count_matches_landmasses_plus_towns_plus_ocean_plus_interiors(pirate_zones):
    world, places = pirate_zones
    water = world["water"]
    land_comp = _land_component_ids(world)
    landmass_ids = set()
    has_open_water_site = False
    for site in world["sites"]:
        if site.get("host"):
            continue
        if water[site["y"]][site["x"]]:
            has_open_water_site = True
        else:
            landmass_ids.add(land_comp[site["y"]][site["x"]])
    n_interiors = sum(1 for s in world["sites"] if s.get("host"))
    n_towns = sum(1 for s in world["sites"] if s["type"] == "settlement")
    n_ocean = 1 if (has_open_water_site or len(landmass_ids) > 1) else 0
    expected = len(landmass_ids) + n_towns + n_ocean + n_interiors
    assert len(places["places"]) == expected


def test_ocean_roundtrip(pirate_zones):
    world, places = pirate_zones
    place_map = places["places"]
    sail_hotspots = [(pid, h) for pid, place in place_map.items()
                      for h in place["interactables"] if h["id"].startswith("h_sail_")]
    assert sail_hotspots, "expected at least one set-sail hotspot"
    for island_id, sail in sail_hotspots:
        ocean_id = sail["action"]["target"]
        ocean_place = place_map[ocean_id]
        spawn = sail["action"]["spawn"]["cell"]
        assert (spawn["x"], spawn["y"]) in _open_cells(ocean_place), island_id

        landfall = next(h for h in ocean_place["interactables"]
                         if h["id"] == f"h_landfall_{island_id}")
        assert landfall["action"]["target"] == island_id
        back_spawn = landfall["action"]["spawn"]["cell"]
        island_place = place_map[island_id]
        assert (back_spawn["x"], back_spawn["y"]) in _open_cells(island_place), island_id


def test_elevation_layout_matches_tiles_dims(pirate_zones):
    _world, places = pirate_zones
    for pid, place in places["places"].items():
        if place["kind"] in ("interior", "town"):
            assert "layout" not in place, pid
            continue
        rows = place["tiles"]["rows"]
        elevation = place["elevation"]
        assert len(place["layout"]["window"]) == 4
        assert isinstance(place["sea_level"], float), pid
        assert len(elevation) == len(rows), pid
        assert all(len(erow) == len(rows[0]) for erow in elevation), pid


def _dual_role_themes(places):
    bad = []
    for pid, place in places["places"].items():
        roles = {}
        for spec in place["tiles"]["legend"].values():
            roles.setdefault(spec["theme"], set()).add(spec["role"])
        bad += [(pid, t) for t, rs in roles.items() if len(rs) > 1]
    return bad


def test_no_theme_is_both_open_and_blocked(pirate_zones):
    _world, places = pirate_zones
    assert _dual_role_themes(places) == []


def test_no_adjacent_hotspots(pirate_zones):
    """The flag-pile bug: two portals side by side. Hotspots reserve with a spacing pass, so
    no two interactables in a place may sit 4-adjacent."""
    _world, places = pirate_zones
    for pid, place in places["places"].items():
        cells = [(h["position"]["cell"]["x"], h["position"]["cell"]["y"])
                 for h in place["interactables"]]
        for i, (x, y) in enumerate(cells):
            for ox, oy in cells[i + 1:]:
                assert abs(x - ox) + abs(y - oy) > 1, (pid, (x, y), (ox, oy))


def test_settlement_hosted_interior_enters_through_town_building(pirate_zones):
    """The tavern is entered from INSIDE the home_port town, at its own building's doorstep —
    never from the world map."""
    world, places = pirate_zones
    town = places["places"]["home_port"]
    assert town["kind"] == "town"
    enter = next(h for h in town["interactables"] if h["id"] == "h_enter_tavern")
    cell = (enter["position"]["cell"]["x"], enter["position"]["cell"]["y"])
    bld = town["footprints"]["bld_tavern"]
    door_zone = (bld["x"] - 1, bld["y"] - 1, bld["x"] + bld["w"], bld["y"] + bld["h"])
    assert door_zone[0] <= cell[0] <= door_zone[2] and door_zone[1] <= cell[1] <= door_zone[3]
    region = places["places"]["home_port_region"]
    assert not any(h["action"].get("target") == "tavern" for h in region["interactables"])


def test_settlement_renders_on_landmass(pirate_zones):
    """A settlement is visible on its landmass map: a footprint entry (the town sprite) and,
    unless blocking would sever the walk graph, blocked rooftop cells under it."""
    _world, places = pirate_zones
    region = places["places"]["home_port_region"]
    fp = region["footprints"]["home_port"]
    rows = region["tiles"]["rows"]
    assert 0 <= fp["x"] and fp["x"] + fp["w"] <= len(rows[0])
    assert 0 <= fp["y"] and fp["y"] + fp["h"] <= len(rows)
    assert fp["label"]


def test_town_gate_roundtrip(pirate_zones):
    _world, places = pirate_zones
    town = places["places"]["home_port"]
    region = places["places"]["home_port_region"]
    leaves = [h for h in town["interactables"] if h["id"].startswith("h_leave_")]
    assert leaves
    for leave in leaves:
        assert leave["action"]["target"] == "home_port_region"
        spawn = leave["action"]["spawn"]["cell"]
        assert (spawn["x"], spawn["y"]) in _open_cells(region)
    enter = next(h for h in region["interactables"] if h["id"] == "h_enter_home_port")
    spawn = enter["action"]["spawn"]["cell"]
    assert (spawn["x"], spawn["y"]) in _open_cells(town)


_FAIRY_RECIPE = {
    "archetype": "continent",
    "size": "small",
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
    ],
}


def test_fairy_recipe_builds_valid_zones():
    """Single-landmass continent, no open_water site: ONE world_map place carries every
    non-settlement site's marker, the settlement is its own town place entered from it, and
    the interior is entered through its building inside the town — no ocean. Seed 4 is pinned
    (rather than generate_best over _SEEDS) because this scenario needs a single landmass and
    generate_best's score-driven seed choice for this recipe lands on a two-landmass world.
    """
    recipe = copy.deepcopy(_FAIRY_RECIPE)
    world = worldgen.generate(recipe, seed=4)
    places = build_zones(world, recipe)
    assert v_places(places) is None
    assert _dual_role_themes(places) == []

    interior_ids = {s["id"] for s in world["sites"] if s.get("host")}
    assert set(places["places"]) == interior_ids | {"fairy_village", "fairy_village_region"}
    region = places["places"]["fairy_village_region"]
    town = places["places"]["fairy_village"]
    assert region["kind"] == "world_map"
    assert town["kind"] == "town"
    assert places["start_place"] == "fairy_village"

    located = [s for s in world["sites"] if not s.get("host")]
    for site in located:
        if site["type"] == "settlement":
            continue
        marker_id = f"h_{site['id']}_marker"
        assert any(h["id"] == marker_id for h in region["interactables"]), site["id"]

    region_targets = {h["action"]["target"] for h in region["interactables"]
                      if h["action"]["type"] == "move"}
    assert region_targets == {"fairy_village"}
    town_targets = {h["action"]["target"] for h in town["interactables"]
                    if h["action"]["type"] == "move"}
    assert town_targets == interior_ids | {"fairy_village_region"}
