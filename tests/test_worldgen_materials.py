"""The pair of squares a region is covered in.

A region has two grounds — the surface and what it wears through to — and every
stage downstream of this one has to be able to find both by name. The image
model is faked: what is under test is that two squares are asked for, quilted,
derived from and named, not what a diffusion model draws.
"""

import json

import numpy as np
import pytest
from PIL import Image

from maestro.worldgen.terrain import materials
from maestro.worldgen.terrain.models import TerrainPlan

PLAN = {
    "world": {"size_m": 40.0, "sea_level_m": -1.0, "boundary_blend_m": 2.0,
              "heightmap_resolution": 256},
    "layout": [{"region_id": "meadow", "category": "grassland", "center": [0.5, 0.5],
                "radius": 0.5, "falloff": 0.5, "coverage": 1.0}],
    "terrain": [{"region_id": "meadow", "base_elevation_m": 1.0}],
    "materials": [{"region_id": "meadow", "surface": "cropped green grass",
                   "appearance": "Soft and even.", "variant": "bare brown earth",
                   "scale_m": 2.0, "pathway": "generative"}],
    "concept": "A small green meadow under a clear sky.",
}


class FakeImages:
    """Writes a noise square wherever it is pointed, and remembers the prompts."""

    def __init__(self):
        self.prompts = []

    def generate(self, prompt, out, **kwargs):
        self.prompts.append(prompt)
        rng = np.random.default_rng(kwargs.get("seed", 0))
        Image.fromarray(rng.integers(0, 255, (256, 256, 3), dtype=np.uint8)).save(out)
        return out


@pytest.fixture
def small_tiles(monkeypatch):
    """Quilt something small: the synthesis is O(tile^2) and is not what is tested."""
    monkeypatch.setattr(materials, "TILE", 96)


def test_a_region_gets_a_base_and_a_variant(tmp_path, small_tiles):
    images = FakeImages()
    made = materials.albedo(TerrainPlan.model_validate(PLAN), tmp_path, images=images)

    assert made["meadow"]["base"] == tmp_path / "meadow_albedo.png"
    assert made["meadow"]["variant"] == tmp_path / "meadow_variant_albedo.png"
    for path in made["meadow"].values():
        assert Image.open(path).size == (96, 96)  # quilted, not the raw render

    surfaces = sorted(images.prompts)
    assert len(surfaces) == 2
    assert "cropped green grass" in surfaces[1]
    assert "bare brown earth" in surfaces[0]
    # the law the lake render broke: a square of ground, never a view of a place
    for prompt in images.prompts:
        assert "no horizon" in prompt


def test_the_manifest_names_both_and_every_derived_channel(tmp_path, small_tiles):
    manifest = materials.build(
        TerrainPlan.model_validate(PLAN), tmp_path, images=FakeImages()
    )
    row = json.loads(manifest.read_text())[0]

    assert row["region_id"] == "meadow"
    assert row["variant"] == "bare brown earth"
    for key, name in (
        ("albedo", "meadow_albedo.png"),
        ("normal", "meadow_normal.png"),
        ("roughness", "meadow_roughness.png"),
        ("variant_albedo", "meadow_variant_albedo.png"),
        ("variant_normal", "meadow_variant_normal.png"),
        ("variant_roughness", "meadow_variant_roughness.png"),
    ):
        assert row[key].endswith(name)
        assert (tmp_path / name).exists()


def test_a_refused_variant_leaves_the_base_alone_in_the_manifest(tmp_path, small_tiles):
    """A render the safety screen drops is one missing file, not a failed stage."""
    images = FakeImages()
    real = images.generate

    def refuse_the_variant(prompt, out, **kwargs):
        if "bare brown earth" in prompt:
            return out  # nothing written, which is what a dropped render looks like
        return real(prompt, out, **kwargs)

    images.generate = refuse_the_variant
    manifest = materials.build(TerrainPlan.model_validate(PLAN), tmp_path, images=images)
    row = json.loads(manifest.read_text())[0]

    assert row["albedo"].endswith("meadow_albedo.png")
    assert "variant_albedo" not in row
