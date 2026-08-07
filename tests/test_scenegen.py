import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from scenegen import BuildingKit, assemble, compose_scene, glade_layout, town_layout
from scenegen.compose import zone_masks
from scenegen.scatter import Scatterer


def _part(w, h, color):
    im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    ImageDraw.Draw(im).rectangle([2, 2, w - 3, h - 3], fill=color + (255,))
    return im


@pytest.fixture
def kit():
    return BuildingKit(door=_part(30, 40, (80, 50, 20)), window=_part(28, 28, (200, 220, 120)))


def test_every_town_door_opens_onto_road():
    for seed in (11, 42, 77, 5):
        lay = town_layout(26, 18, seed)
        assert lay.buildings, f"seed {seed} placed no buildings"
        for gx, gy, wc, hw in lay.buildings:
            assert lay.road[gy + hw, gx + wc // 2], f"seed {seed}: door faces no road"


def test_town_lots_do_not_overlap():
    lay = town_layout(26, 18, 42)
    claimed = np.zeros((18, 26), bool)
    for gx, gy, wc, hw in lay.buildings:
        region = claimed[gy:gy + hw, gx:gx + wc]
        assert not region.any()
        claimed[gy:gy + hw, gx:gx + wc] = True


def test_same_seed_same_town():
    a, b = town_layout(26, 18, 7), town_layout(26, 18, 7)
    assert a.buildings == b.buildings
    assert (a.road == b.road).all()


def test_assemble_returns_the_door_cell(kit):
    pil = Image.new("RGB", (26 * 48, 18 * 48), (0, 100, 0))
    door = assemble(pil, kit, gx=6, gy=4, wc=3, hw=2)
    assert door == (7, 6)
    # the door sprite is actually on that cell
    px = np.asarray(pil)[5 * 48 + 40, 7 * 48 + 24]
    assert tuple(px) == (80, 50, 20)


def test_compose_scene_renders_and_reports_doors(kit):
    lay = town_layout(26, 18, 42)
    img, doors = compose_scene(lay, kit)
    assert img.size == (26 * 48, 18 * 48)
    assert len(doors) == len(lay.buildings)


def test_scatter_respects_spacing_and_zones(kit):
    lay = town_layout(26, 18, 42)
    img, _ = compose_scene(lay, kit)
    zones = zone_masks(lay)
    sc = Scatterer(img, seed=1)
    n = sc.scatter(_part(20, 20, (255, 0, 0)), zones["along_roads"], count=8, spacing=100,
                   jitter=0)
    assert n > 0
    pts = sc.occupied
    for i, (x, y) in enumerate(pts):
        assert zones["along_roads"][y, x]
        for a, b in pts[i + 1:]:
            assert (x - a) ** 2 + (y - b) ** 2 > 100 * 100


def test_glade_layout_is_seeded_and_contains_its_pond():
    a = glade_layout(800, 600, 3)
    b = glade_layout(800, 600, 3)
    assert (a.pond == b.pond).all()
    assert a.pond.sum() > 0
    assert (a.pond & ~a.clearing).sum() == 0


def test_bake_scene_writes_ground_and_logic(tmp_path):
    from scenegen.bake import bake_scene
    res = bake_scene(tmp_path, "town1", "town", "painted cartoon style, cozy village", seed=42)
    assert res["ok"]
    import json as _json
    data = _json.loads((tmp_path / "assets/town1_scene.json").read_text())
    assert (tmp_path / "assets/town1_ground.png").exists()
    assert data["doors"] and data["cell_px"] == 48
    for x, y in data["doors"]:
        assert data["walkable"][y][x] == "1"


def test_bake_scene_archetypes_all_render(tmp_path):
    from scenegen.bake import bake_scene
    for arch in ("glade", "interior", "dungeon"):
        res = bake_scene(tmp_path, f"s_{arch}", arch, "dark gothic", seed=3)
        assert res["ok"], arch
        assert (tmp_path / f"assets/s_{arch}_ground.png").exists()


def test_bake_scene_rejects_unknown_archetype(tmp_path):
    from scenegen.bake import bake_scene
    res = bake_scene(tmp_path, "x", "spaceship", "chrome", seed=1)
    assert not res["ok"]
