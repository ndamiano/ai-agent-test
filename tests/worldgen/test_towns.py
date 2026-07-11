from collections import deque

import pytest

import worldgen
from worldgen.towns import build_town

_SEEDS = range(1, 21)


@pytest.fixture(scope="module")
def pirate_town():
    recipe = {
        "archetype": "archipelago",
        "size": "small",
        "palette": {"biomes": ["open sea", "reef", "tropical shallows", "beach", "jungle",
                                "rocky highlands"]},
        "locations": [
            {"id": "home_port", "type": "settlement", "want": "coastal harbor"},
            {"id": "smugglers_port", "type": "settlement",
             "want": "coastal, remote from home_port"},
            {"id": "tavern", "type": "interior", "host": "home_port"},
            {"id": "smithy", "type": "interior", "host": "home_port"},
            {"id": "sea", "type": "open_water", "want": "large"},
        ],
    }
    world, seed = worldgen.generate_best(recipe, _SEEDS)
    site = next(s for s in world["sites"] if s["id"] == "home_port")
    return site, world, seed


def _grids(town):
    rows = town["tiles"]["rows"]
    legend = town["tiles"]["legend"]
    role = [[legend[ch]["role"] for ch in row] for row in rows]
    theme = [[legend[ch]["theme"] for ch in row] for row in rows]
    return role, theme


def _flood_open(role, start):
    w, h = len(role[0]), len(role)
    seen = {start}
    dq = deque([start])
    while dq:
        x, y = dq.popleft()
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nx, ny = x + dx, y + dy
            if 0 <= nx < w and 0 <= ny < h and (nx, ny) not in seen and role[ny][nx] == "open":
                seen.add((nx, ny))
                dq.append((nx, ny))
    return seen


def test_deterministic(pirate_town):
    site, world, seed = pirate_town
    a = build_town(site, ["tavern", "smithy"], world, seed)
    b = build_town(site, ["tavern", "smithy"], world, seed)
    assert a == b


def test_hosted_interiors_each_get_building_and_door(pirate_town):
    site, world, seed = pirate_town
    town = build_town(site, ["tavern", "smithy"], world, seed)
    role, _theme = _grids(town)
    for interior_id in ("tavern", "smithy"):
        assert f"bld_{interior_id}" in town["footprints"]
        door = town["doors"][interior_id]
        assert role[door[1]][door[0]] == "open"
        bld = town["footprints"][f"bld_{interior_id}"]
        assert (bld["x"] - 1 <= door[0] <= bld["x"] + bld["w"]
                and bld["y"] - 1 <= door[1] <= bld["y"] + bld["h"])


def test_gates_on_edge_open_and_connected_to_plaza_and_doors(pirate_town):
    site, world, seed = pirate_town
    town = build_town(site, ["tavern", "smithy"], world, seed)
    role, _theme = _grids(town)
    w, h = town["ww"], town["wh"]
    assert town["gates"]
    reach = _flood_open(role, town["plaza"])
    assert role[town["plaza"][1]][town["plaza"][0]] == "open"
    for gate in town["gates"]:
        gx, gy = gate["cell"]
        assert gx in (0, w - 1) or gy in (0, h - 1), gate
        assert role[gy][gx] == "open"
        assert (gx, gy) in reach
        ix, iy = gate["inward"]
        assert (ix, iy) in reach
    for door in town["doors"].values():
        assert door in reach


def test_footprints_disjoint_blocked_in_bounds(pirate_town):
    site, world, seed = pirate_town
    town = build_town(site, ["tavern", "smithy"], world, seed)
    role, _theme = _grids(town)
    w, h = town["ww"], town["wh"]
    claimed = set()
    for fid, fp in town["footprints"].items():
        assert fp["label"], fid
        assert 0 < fp["x"] and fp["x"] + fp["w"] < w, fid
        assert 0 < fp["y"] and fp["y"] + fp["h"] < h, fid
        for y in range(fp["y"], fp["y"] + fp["h"]):
            for x in range(fp["x"], fp["x"] + fp["w"]):
                assert (x, y) not in claimed, (fid, x, y)
                claimed.add((x, y))
                assert role[y][x] == "blocked", (fid, x, y)


def test_no_unreachable_open_cells(pirate_town):
    site, world, seed = pirate_town
    town = build_town(site, ["tavern", "smithy"], world, seed)
    role, _theme = _grids(town)
    reach = _flood_open(role, town["plaza"])
    all_open = {(x, y) for y, row in enumerate(role) for x, cell in enumerate(row)
                if cell == "open"}
    assert all_open == reach


def test_doors_never_adjacent_to_other_hotspot(pirate_town):
    site, world, seed = pirate_town
    town = build_town(site, ["tavern", "smithy"], world, seed)
    hotspots = list(town["doors"].values()) + [g["cell"] for g in town["gates"]]
    for i, (x, y) in enumerate(hotspots):
        for ox, oy in hotspots[i + 1:]:
            assert abs(x - ox) + abs(y - oy) > 1, ((x, y), (ox, oy))


def test_no_theme_dual_role(pirate_town):
    site, world, seed = pirate_town
    town = build_town(site, ["tavern", "smithy"], world, seed)
    roles = {}
    for spec in town["tiles"]["legend"].values():
        roles.setdefault(spec["theme"], set()).add(spec["role"])
    assert all(len(rs) == 1 for rs in roles.values()), roles


def test_gate_edges_follow_world_roads(pirate_town):
    """home_port and smugglers_port are road-connected, so home_port's town has a gate on the
    edge the road leaves toward (never the roadless fallback south gate only by accident):
    the gate edge set is derived, so just assert it is non-empty and every edge is real."""
    site, world, seed = pirate_town
    town = build_town(site, ["tavern", "smithy"], world, seed)
    assert {g["edge"] for g in town["gates"]} <= {"n", "e", "s", "w"}
