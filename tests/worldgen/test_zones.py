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
    """No arbitrary per-site crops: every located site's marker lives on the ONE place for its
    landmass (or the ocean place, if it sits on water), never a bespoke window of its own."""
    world, places = pirate_zones
    water = world["water"]
    land_comp = _land_component_ids(world)
    landmass_place = {}
    for site in world["sites"]:
        if site.get("host"):
            continue
        pid = _marker_place(places, site["id"])
        if water[site["y"]][site["x"]]:
            continue
        cid = land_comp[site["y"]][site["x"]]
        prev = landmass_place.get(cid)
        if prev is None:
            landmass_place[cid] = pid
        else:
            assert prev == pid, (site["id"], cid, prev, pid)


def test_zone_count_matches_landmasses_plus_ocean_plus_interiors(pirate_zones):
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
    n_ocean = 1 if (has_open_water_site or len(landmass_ids) > 1) else 0
    expected = len(landmass_ids) + n_ocean + n_interiors
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
        if place["kind"] == "interior":
            assert "layout" not in place, pid
            continue
        layout = place["layout"]
        rows = place["tiles"]["rows"]
        elevation = layout["elevation"]
        assert len(layout["window"]) == 4
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
    """Single-landmass continent, no open_water site: ONE outdoor place carries every site's
    marker plus the interior — no ocean, and no zone-to-zone move hotspot at all (the old bug
    was two adjacent move-markers to different beaches on the SAME island). Seed 3 is pinned
    (rather than generate_best over _SEEDS) because this scenario needs a single landmass and
    generate_best's score-driven seed choice for this recipe lands on a two-landmass world.
    """
    recipe = copy.deepcopy(_FAIRY_RECIPE)
    world = worldgen.generate(recipe, seed=3)
    places = build_zones(world, recipe)
    assert v_places(places) is None
    assert _dual_role_themes(places) == []

    interior_ids = {s["id"] for s in world["sites"] if s.get("host")}
    assert set(places["places"]) == interior_ids | {"fairy_village"}
    outdoor_ids = set(places["places"]) - interior_ids
    assert len(outdoor_ids) == 1, outdoor_ids
    outdoor_place = places["places"][next(iter(outdoor_ids))]

    located = [s for s in world["sites"] if not s.get("host")]
    for site in located:
        marker_id = f"h_{site['id']}_marker"
        assert any(h["id"] == marker_id for h in outdoor_place["interactables"]), site["id"]

    move_targets = {h["action"]["target"] for h in outdoor_place["interactables"]
                     if h["action"]["type"] == "move"}
    assert move_targets == interior_ids
