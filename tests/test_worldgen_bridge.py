"""worldgen bridge: the generated world.ts must carry the whole world — village + wilderness ring
(forest trees, POIs, roads, regions) — with every coordinate on the heightfield."""

import pytest

from maestro.codegen import worldgen_bridge

RECIPE = {"archetype": "continent", "size": "small",
          "palette": {"biomes": ["grassland", "forest", "hill"]},
          "locations": [{"id": "testville", "type": "settlement", "name": "Testville"}]}


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    out = tmp_path_factory.mktemp("wg") / "game"
    return worldgen_bridge.build(RECIPE, out), out


def test_grid_is_town_plus_ring(world):
    data, _ = world
    assert data["gw"] > 2 * worldgen_bridge.RING
    assert data["gh"] > 2 * worldgen_bridge.RING
    assert len(data["height"]) == data["gh"]
    assert len(data["height"][0]) == data["gw"]


def test_wilderness_content_exists(world):
    data, _ = world
    assert len(data["pois"]) == 3
    assert {p["kind"] for p in data["pois"]} == {"cave", "ruins", "camp"}
    assert all(p["parts"] for p in data["pois"])
    assert len(data["trees"]) > 20
    assert len(data["road"]) > 5
    assert data["regions"]["forest"] and data["regions"]["meadow"]


def test_everything_sits_on_the_grid(world):
    data, _ = world
    half_x = data["gw"] * data["cell"] / 2
    half_z = data["gh"] * data["cell"] / 2

    def on_grid(x, z):
        return -half_x <= x <= half_x and -half_z <= z <= half_z

    for b in data["buildings"]:
        assert on_grid(b["x"], b["z"])
    for p in data["pois"]:
        assert on_grid(p["x"], p["z"])
    for t in data["trees"]:
        assert on_grid(t[0], t[1])
    for pts in data["regions"].values():
        for x, z in pts:
            assert on_grid(x, z)
    assert on_grid(data["plaza"]["x"], data["plaza"]["z"])
    assert on_grid(data["gate"]["x"], data["gate"]["z"])


def test_pois_are_outside_the_town(world):
    data, _ = world
    tw = (data["gw"] - 2 * worldgen_bridge.RING) * data["cell"] / 2
    th = (data["gh"] - 2 * worldgen_bridge.RING) * data["cell"] / 2
    for p in data["pois"]:
        assert abs(p["x"]) > tw or abs(p["z"]) > th, f"POI {p['id']} landed inside the town"


def test_world_ts_exports_the_new_surface(world):
    _, out = world
    src = (out / "world.ts").read_text(encoding="utf-8")
    for key in ("pois", "regions", "trees", "road"):
        assert f'"{key}"' in src
    assert "WORLD.trees" in src and "WORLD.pois" in src   # spawnWorld renders them
