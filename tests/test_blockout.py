import numpy as np
import pytest
from scipy import ndimage as ndi

from scenegen.blockout import solve

PLAN = {
    "terrain": {"base": "grass", "features": ["water_edge:south"]},
    "placeables": [
        {"name": "The Salt Barrel inn", "kind": "building", "size": "medium"},
        {"name": "cottage", "kind": "building", "size": "small", "count": 3},
        {"name": "old lighthouse", "kind": "landmark", "size": "large"},
        {"name": "fish market", "kind": "zone", "size": "large"},
        {"name": "pine tree", "kind": "decoration", "size": "small", "count": 4},
    ],
    "constraints": [
        ["near", "The Salt Barrel inn", "fish market"],
        ["central", "fish market"],
        ["on_edge", "old lighthouse", "south"],
    ],
}


@pytest.fixture(scope="module")
def result():
    return solve(PLAN, "a small fishing village", 7)


def _walk_grid(scene):
    return np.array([[c == "1" for c in row] for row in scene["walkable"]])


def test_grid_dims(result):
    b, s = result["blockout"], result["scene"]
    assert b["cells"] == [48, 36]
    assert len(b["terrain_grid"]) == 36
    assert all(len(row) == 48 for row in b["terrain_grid"])
    assert s["width_cells"] == 48 and s["height_cells"] == 36
    assert len(s["walkable"]) == 36
    assert all(len(row) == 48 for row in s["walkable"])


def test_roads_exist(result):
    assert "road" in result["blockout"]["terrain_names"]
    road_idx = result["blockout"]["terrain_names"].index("road")
    assert any(str(road_idx) in row for row in result["blockout"]["terrain_grid"])


def test_boxes_in_bounds_and_walls_disjoint(result):
    boxes = result["blockout"]["boxes"]
    assert boxes
    for b in boxes:
        assert 0 <= b["x"] and 0 <= b["y"]
        assert b["x"] + b["w"] <= 48 and b["y"] + b["h"] <= 36
    walls = [b for b in boxes if b["kind"] == "building"]
    assert walls
    for i, a in enumerate(walls):
        for b in walls[i + 1:]:
            overlap_x = max(0, min(a["x"] + a["w"], b["x"] + b["w"]) - max(a["x"], b["x"]))
            overlap_y = max(0, min(a["y"] + a["h"], b["y"] + b["h"]) - max(a["y"], b["y"]))
            assert overlap_x * overlap_y == 0


def test_walkable_single_component(result):
    walk = _walk_grid(result["scene"])
    assert walk.any()
    _, n = ndi.label(walk)
    assert n == 1


def test_pois_on_walkable(result):
    walk = _walk_grid(result["scene"])
    assert result["scene"]["pois"]
    for p in result["scene"]["pois"]:
        assert walk[p["y"], p["x"]]


def test_doors_match_buildings(result):
    n_buildings = sum(1 for b in result["blockout"]["boxes"] if b["kind"] == "building")
    assert len(result["scene"]["doors"]) == n_buildings


@pytest.mark.parametrize("bad", [None, {}, {"terrain": {}}, {"placeables": []}])
def test_malformed_plan_falls_back(bad):
    out = solve(bad, "anywhere", 3)
    walk = _walk_grid(out["scene"])
    assert walk.any()
    assert out["blockout"]["boxes"]


def test_determinism():
    a = solve(PLAN, "a small fishing village", 11)
    b = solve(PLAN, "a small fishing village", 11)
    assert a == b
