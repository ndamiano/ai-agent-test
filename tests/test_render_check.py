"""A finished render, checked for BROKEN — never for bad.

Code cannot tell whether a picture suits the game; that is the human's question and always was. What
it can read is the alpha channel against the kind that was asked for. A matted sprite that came back
fully opaque is a scene the matte never cut, and the game composites a square of someone else's
background wherever it draws it; a tile that came back half transparent is a floor the matte ate.
Both shipped in a real build before the kinds existed.
"""

import pytest
from PIL import Image

import maestro.state
from db import store
from maestro.codegen import asset_chain
from maestro.codegen.assets import check_render, read_manifest, request_media
from maestro.state import RunState

RUN = "r1"


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "_db_path", lambda: tmp_path / "platform.db")
    monkeypatch.setattr(maestro.state, "resolve_base_path", lambda input_path=None: tmp_path)
    store.create_game(RUN, "u1")
    store.charge_game(RUN, 1, 10_000.0)


@pytest.fixture
def run_dir(tmp_path):
    d = RunState(RUN).run_dir
    (d / "game").mkdir(parents=True, exist_ok=True)
    return d


def _png(path, alpha, size=(64, 64)):
    Image.new("RGBA", size, (120, 90, 60, alpha)).save(path)
    return path


def test_an_opaque_sprite_is_a_matte_that_never_ran(tmp_path):
    defect = check_render(_png(tmp_path / "a.png", 255), "sprite")
    assert defect and "background was not removed" in defect


def test_an_empty_sprite_is_a_matte_that_ate_the_subject(tmp_path):
    assert "almost nothing is left" in check_render(_png(tmp_path / "a.png", 0), "sprite")


def test_a_matted_sprite_passes(tmp_path):
    im = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    im.paste((120, 90, 60, 255), (8, 8, 56, 56))
    im.save(tmp_path / "a.png")
    assert check_render(tmp_path / "a.png", "sprite") is None


def test_a_transparent_tile_is_a_hole(tmp_path):
    defect = check_render(_png(tmp_path / "a.png", 0), "tile")
    assert defect and "fill its frame" in defect


@pytest.mark.parametrize("kind", ["tile", "scene"])
def test_a_full_frame_background_passes(tmp_path, kind):
    assert check_render(_png(tmp_path / "a.png", 255), kind) is None


def test_a_mesh_is_checked_as_the_sprite_its_image_leg_renders(tmp_path):
    """TRELLIS lifts a matted subject into geometry, so the image leg of a mesh is a sprite and an
    opaque one is the same defect."""
    assert check_render(_png(tmp_path / "a.png", 255), "sprite") is not None


def test_a_defect_lands_on_the_manifest_entry(run_dir, tmp_path):
    request_media(RUN, run_dir, "hero", "a hero", kind="sprite")
    src = _png(tmp_path / "out.png", 255)
    asset_chain.run_operations({"run_id": RUN, "asset_id": "hero", "kind": "sprite",
                                "then": {"operations": ["save_sprite"]}},
                               {"images": [{"file": str(src), "safety": {"scores": {"NSFW": 0.0, "SFW": 1.0}}}]})
    (entry,) = read_manifest(run_dir)
    assert "background was not removed" in entry["defect"]


def test_a_clean_render_leaves_no_defect(run_dir, tmp_path):
    request_media(RUN, run_dir, "floor", "worn floorboards", kind="tile")
    src = _png(tmp_path / "out.png", 255)
    asset_chain.run_operations({"run_id": RUN, "asset_id": "floor", "kind": "tile",
                                "then": {"operations": ["save_flat"]}},
                               {"images": [{"file": str(src), "safety": {"scores": {"NSFW": 0.0, "SFW": 1.0}}}]})
    (entry,) = read_manifest(run_dir)
    assert entry.get("defect") is None


def test_a_flat_kind_is_saved_whole(run_dir, tmp_path):
    """save_flat does not autocrop: cropping a tile to its "subject" is how a floor becomes a
    handful of planks."""
    request_media(RUN, run_dir, "floor", "worn floorboards", kind="tile")
    src = _png(tmp_path / "out.png", 255, size=(128, 128))
    asset_chain.run_operations({"run_id": RUN, "asset_id": "floor", "kind": "tile",
                                "then": {"operations": ["save_flat"]}},
                               {"images": [{"file": str(src), "safety": {"scores": {"NSFW": 0.0, "SFW": 1.0}}}]})
    assert Image.open(run_dir / "game" / "assets" / "floor.webp").size == (128, 128)
