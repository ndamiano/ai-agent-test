"""The world contract and the browser that draws it.

`write_job` is checked on a world small enough to write by hand, because the one
thing the job must never contain is a path that only resolves on the machine
that made it. The render test then runs the real pipeline — server, chromium,
three.js — on that same world, and is skipped where there is no browser.
"""

import json
import struct

import numpy as np
import pytest
from PIL import Image

from maestro.worldgen.terrain.models import TerrainPlan
from maestro.worldgen.terrain.render import JOB_NAME, render, write_job

PLAN = {
    "world": {"size_m": 40.0, "sea_level_m": -1.0, "boundary_blend_m": 2.0,
              "heightmap_resolution": 256},
    "layout": [{"region_id": "meadow", "category": "grassland", "center": [0.5, 0.5],
                "radius": 0.5, "falloff": 0.5, "coverage": 1.0}],
    "terrain": [{"region_id": "meadow", "base_elevation_m": 1.0}],
    "materials": [{"region_id": "meadow", "surface": "grass", "appearance": "green",
                   "scale_m": 2.0, "pathway": "procedural"}],
    "concept": "A small green meadow under a clear sky.",
}


@pytest.fixture
def world(tmp_path):
    """A 16x16 world with one region and one scattered thing with no mesh."""
    height = np.tile(np.linspace(0.0, 4.0, 16, dtype=np.float32), (16, 1))
    np.save(tmp_path / "heightmap.npy", height)
    np.save(tmp_path / "layout_masks.npy", np.ones((1, 16, 16), np.float32))

    materials = tmp_path / "materials"
    materials.mkdir()
    for channel, colour in (("albedo", (90, 140, 60)), ("normal", (128, 128, 255))):
        Image.new("RGB", (8, 8), colour).save(materials / f"meadow_{channel}.png")
    (materials / "materials.json").write_text(json.dumps([{
        "region_id": "meadow",
        "albedo": "/gone/meadow_albedo.png",
        "normal": "/gone/meadow_normal.png",
    }]))
    (tmp_path / "prototypes.json").write_text(json.dumps([{"category": "rock", "mesh": None}]))
    (tmp_path / "scatter.json").write_text(json.dumps({"instances": [{
        "category": "rock", "position": [20.0, 2.0, 20.0],
        "yaw_deg": 0.0, "scale": 1.0, "height_m": 1.0,
    }]}))
    return tmp_path, TerrainPlan.model_validate(PLAN)


def test_write_job_writes_a_relative_world(world):
    out_dir, plan = world
    path = write_job(plan, out_dir)
    assert path.name == JOB_NAME
    job = json.loads(path.read_text())

    assert set(job) == {
        "heightmap", "resolution", "mesh_resolution", "size_m", "sea_level_m", "lowest_m",
        "sun_elevation_deg", "sun_azimuth_deg", "weight_textures", "regions", "instances",
        "cameras",
    }
    paths = [job["heightmap"], *job["weight_textures"],
             *(p for r in job["regions"] for p in (r["albedo"], r["normal"]))]
    for relative in paths:
        assert not relative.startswith((".", "/"))
        assert (out_dir / relative).exists()

    assert job["resolution"] == 16
    assert job["size_m"] == 40.0
    assert job["lowest_m"] == 0.0
    region = job["regions"][0]
    assert region["region_id"] == "meadow"
    assert region["category"] == "grassland"
    assert region["centre_m"] == [20.0, 20.0]
    assert region["radius_m"] == 20.0
    assert region["scale_m"] == 2.0
    # a category whose reconstruction produced no mesh is simply not in the world
    assert job["instances"] == {}
    assert [shot["name"] for shot in job["cameras"]] == [
        "view_top.png", "view_0.png", "view_1.png", "view_2.png", "view_3.png",
    ]


def test_write_job_rewrites_paths_that_moved(world):
    """The manifest records where the textures were when they were made. A world
    that has been copied must render its own, not the original's."""
    out_dir, plan = world
    job = json.loads(write_job(plan, out_dir).read_text())
    assert job["regions"][0]["albedo"] == "materials/meadow_albedo.png"


def test_heightmap_is_the_float_buffer_the_loader_reads(world):
    out_dir, plan = world
    write_job(plan, out_dir)
    raw = (out_dir / "heightmap.f32").read_bytes()
    assert len(raw) == 16 * 16 * 4
    assert struct.unpack("<f", raw[:4])[0] == pytest.approx(0.0)


def test_render_draws_the_world_in_a_browser(world):
    out_dir, plan = world
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import Error, sync_playwright
    try:
        with sync_playwright() as pw:
            pw.chromium.launch().close()
    except Error as e:
        pytest.skip(f"no chromium: {e}")

    shot = {"name": "view.png", "position": [20.0, 30.0, 60.0],
            "look_at": [20.0, 2.0, 20.0], "fov": 55.0}
    written = render(plan, out_dir, cameras=[shot], resolution="320x200", mesh_resolution=32)

    assert [p.name for p in written] == ["view.png"]
    image = np.asarray(Image.open(written[0]).convert("RGB"))
    assert image.shape == (200, 320, 3)
    # a world drawn near-black is the known 3D failure, and an empty frame is
    # one flat colour; neither is a picture of a meadow
    assert image.mean() > 20
    assert len(np.unique(image.reshape(-1, 3), axis=0)) > 1
