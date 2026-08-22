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


def _box_glb() -> bytes:
    """A 1 m cube as a GLB, written by hand so the tests need no mesh library."""
    corners = [(x, y, z) for y in (0.0, 1.0) for z in (0.0, 1.0) for x in (0.0, 1.0)]
    faces = [(0, 1, 3), (0, 3, 2), (4, 7, 5), (4, 6, 7), (0, 4, 5), (0, 5, 1),
             (2, 3, 7), (2, 7, 6), (0, 2, 6), (0, 6, 4), (1, 5, 7), (1, 7, 3)]
    positions = struct.pack("<24f", *(v for c in corners for v in c))
    indices = struct.pack("<36H", *(i for f in faces for i in f))
    buffer = positions + indices
    gltf = {
        "asset": {"version": "2.0"},
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": [{"mesh": 0}],
        "meshes": [{"primitives": [{"attributes": {"POSITION": 0}, "indices": 1}]}],
        "accessors": [
            {"bufferView": 0, "componentType": 5126, "count": 8, "type": "VEC3",
             "min": [0.0, 0.0, 0.0], "max": [1.0, 1.0, 1.0]},
            {"bufferView": 1, "componentType": 5123, "count": 36, "type": "SCALAR"},
        ],
        "bufferViews": [
            {"buffer": 0, "byteOffset": 0, "byteLength": len(positions), "target": 34962},
            {"buffer": 0, "byteOffset": len(positions), "byteLength": len(indices),
             "target": 34963},
        ],
        "buffers": [{"byteLength": len(buffer)}],
    }
    text = json.dumps(gltf).encode()
    text += b" " * (-len(text) % 4)
    body = buffer + b"\0" * (-len(buffer) % 4)
    chunks = (struct.pack("<II", len(text), 0x4E4F534A) + text
              + struct.pack("<II", len(body), 0x004E4942) + body)
    return struct.pack("<III", 0x46546C67, 2, 12 + len(chunks)) + chunks


@pytest.fixture
def ridge_world(tmp_path):
    """A flat plain with one steep ridge across it, and a tall box standing on it.

    Both rules the renderer applies to a material are visible in one frame: the
    steep face has to stop looking like the flat ground beside it, and the box
    has to darken the ground the sun would otherwise reach.
    """
    size = 64
    x = np.linspace(0.0, 1.0, size, dtype=np.float32)
    # a plain at 0 m with a 20 m wall of ground in the middle third
    height = np.tile(np.clip((x - 0.45) * 40.0, 0.0, 20.0), (size, 1)).astype(np.float32)
    np.save(tmp_path / "heightmap.npy", height)
    np.save(tmp_path / "layout_masks.npy", np.ones((1, size, size), np.float32))

    materials = tmp_path / "materials"
    materials.mkdir()
    for channel, colour in (("albedo", (120, 150, 70)), ("normal", (128, 128, 255))):
        Image.new("RGB", (8, 8), colour).save(materials / f"meadow_{channel}.png")
    (materials / "materials.json").write_text(json.dumps([{
        "region_id": "meadow",
        "albedo": "meadow_albedo.png",
        "normal": "meadow_normal.png",
    }]))
    (tmp_path / "prototypes.json").write_text(json.dumps([]))
    (tmp_path / "scatter.json").write_text(json.dumps({"instances": []}))
    return tmp_path, TerrainPlan.model_validate(PLAN)


def _browser_or_skip():
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import Error, sync_playwright
    try:
        with sync_playwright() as pw:
            pw.chromium.launch().close()
    except Error as e:
        pytest.skip(f"no chromium: {e}")


def test_a_steep_face_is_not_the_colour_of_the_flat_ground(ridge_world):
    """The slope rule, from above so that shading cannot be what is measured.

    A top-down camera sees the flat plain and the steep face under the same sun
    at the same angle to the eye. If the two read the same, the only thing the
    ground is textured by is its region.
    """
    _browser_or_skip()
    out_dir, plan = ridge_world
    shot = {"name": "top.png", "position": [20.0, 40.0, 20.0],
            "look_at": [20.0, 0.0, 20.0], "fov": 80.0}
    written = render(plan, out_dir, cameras=[shot], resolution="256x256", mesh_resolution=64)
    image = np.asarray(Image.open(written[0]).convert("RGB"), np.float32)

    # the ridge runs north-south at x ~ 0.45..0.95 of the world, so in a
    # top-down frame the flat ground is the left band and the face the middle
    flat = image[64:192, 40:80]
    steep = image[64:192, 128:168]
    assert steep.mean() < flat.mean() * 0.9, (steep.mean(), flat.mean())
    # ...and it is a rock's desaturation, not only a darkening
    def spread(patch):
        return float(patch.max(axis=-1).mean() - patch.min(axis=-1).mean())
    assert spread(steep) < spread(flat) * 0.8, (spread(steep), spread(flat))


def test_a_tall_thing_casts_a_shadow_on_the_ground(ridge_world, monkeypatch):
    """The sun the loader hangs over a world reaches the terrain material.

    A raw shader receives no shadow map, so this is what tells us the terrain is
    still a material three lights rather than one lit beside the world.
    """
    _browser_or_skip()
    out_dir, plan = ridge_world
    # a 10 m post in the middle of the flat half, and nothing else
    (out_dir / "prototypes.json").write_text(json.dumps([
        {"category": "post", "mesh": "subjects/meshes/post.glb"},
    ]))
    (out_dir / "scatter.json").write_text(json.dumps({"instances": [{
        "category": "post", "position": [10.0, 0.0, 20.0],
        "yaw_deg": 0.0, "scale": 1.0, "height_m": 10.0,
    }]}))
    meshes = out_dir / "subjects" / "meshes"
    meshes.mkdir(parents=True)
    meshes.joinpath("post.glb").write_bytes(_box_glb())

    shot = {"name": "shadow.png", "position": [10.0, 26.0, 20.0],
            "look_at": [10.0, 0.0, 20.0], "fov": 70.0}
    written = render(plan, out_dir, cameras=[shot], resolution="256x256", mesh_resolution=64)
    image = np.asarray(Image.open(written[0]).convert("L"), np.float32)

    # the sun is at azimuth 135 and 42 degrees up, so the post's shadow falls to
    # -x and +z of it; a camera looking straight down draws that below and to
    # the left of the post, and the same ground above the post stays lit
    shaded = image[190:240, 70:115]
    lit = image[20:80, 70:115]
    assert shaded.mean() < lit.mean() * 0.75, (shaded.mean(), lit.mean())
