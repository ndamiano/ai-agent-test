import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from scenegen.bake import bake_scene


def test_bake_scene_writes_ground_and_logic(tmp_path):
    res = bake_scene(tmp_path, "inn", "interior", "painted cartoon style, cozy", seed=42)
    assert res["ok"]
    data = json.loads((tmp_path / "assets/inn_scene.json").read_text())
    assert (tmp_path / "assets/inn_ground.png").exists()
    assert data["rooms"] and data["cell_px"] == 48
    assert any("1" in row for row in data["walkable"])


def test_bake_scene_archetypes_all_render(tmp_path):
    for arch in ("interior", "dungeon"):
        res = bake_scene(tmp_path, f"s_{arch}", arch, "dark gothic", seed=3)
        assert res["ok"], arch
        assert (tmp_path / f"assets/s_{arch}_ground.png").exists()


@pytest.mark.parametrize("arch", ["town", "glade", "spaceship"])
def test_bake_scene_rejects_outdoor_and_unknown_archetypes(tmp_path, arch):
    with pytest.raises(ValueError, match=arch):
        bake_scene(tmp_path, "x", arch, "chrome", seed=1)
