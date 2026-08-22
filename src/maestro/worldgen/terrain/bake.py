"""Compositing the region materials into one terrain surface.

"The same regional weights are subsequently used to assign and blend materials
from M_terrain over the corresponding surfaces."

Each region's material is a small square that repeats every `scale_m` metres.
This tiles each one across the whole world at its own scale and adds them
together under the region weights m~_r — the same weights the height field was
built from, so the ground is textured as the region it actually belongs to.

NOT what the renderer uses, and the reason is worth recording. A material that
repeats every 3 m in a 400 m world repeats 133 times; baked into a 2048 px
image that is 15 px per repeat, so a 1024 px source texture is thrown away and
what remains is an obvious grid. No bake resolution fixes this — 133 repeats of
anything detailed needs more pixels than a texture should have.

The renderer therefore blends the materials in a shader at world-space UVs,
where each one is sampled at its own full resolution and never resampled. This
module survives for the one case that cannot do that: exporting a single mesh
with a single texture, for a viewer that will not run our shader. It carries the
repetition as a known cost.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from PIL import Image

from .models import TerrainPlan

RESOLUTION = 2048


def _tiled(texture: Path, repeats: float, resolution: int) -> np.ndarray:
    """One material tiled `repeats` times across the world, as float 0..1."""
    tile = Image.open(texture).convert("RGB")
    # size each tile so that `repeats` of them span the output, then wrap
    side = max(2, int(round(resolution / max(repeats, 1e-3))))
    tile = tile.resize((side, side), Image.LANCZOS)
    single = np.asarray(tile, np.float32) / 255.0
    counts = int(np.ceil(resolution / side))
    return np.tile(single, (counts, counts, 1))[:resolution, :resolution]


def pack_masks(
    plan: TerrainPlan,
    out_dir: Path | str,
    *,
    weights: np.ndarray | None = None,
) -> list[str]:
    """Write the region weights as RGBA textures, four regions to a texture.

    This is what the shader blends by. Four per texture because that is what a
    colour sampler carries, and a world of five regions costing a second texture
    lookup is cheaper than any of the alternatives.
    """
    out_dir = Path(out_dir)
    if weights is None:
        weights = np.load(out_dir / "layout_masks.npy")
    written = []
    for group in range((len(weights) + 3) // 4):
        block = weights[group * 4:(group + 1) * 4]
        if len(block) < 4:
            block = np.concatenate(
                [block, np.zeros((4 - len(block), *block.shape[1:]), np.float32)]
            )
        image = np.clip(np.moveaxis(block, 0, -1), 0.0, 1.0)
        path = out_dir / f"region_weights_{group}.png"
        Image.fromarray((image * 255).astype(np.uint8), "RGBA").save(path)
        written.append(str(path))
    return written


def bake(
    plan: TerrainPlan,
    out_dir: Path | str,
    *,
    weights: np.ndarray | None = None,
    resolution: int = RESOLUTION,
) -> dict[str, str]:
    """Composite albedo, normal and roughness for the whole terrain.

    Returns the paths written. The weights are resampled to the bake resolution,
    which is usually finer than the height field: a texture is looked at from a
    metre away and a height field from a hundred.
    """
    out_dir = Path(out_dir)
    materials = json.loads((out_dir / "materials" / "materials.json").read_text())
    by_region = {}
    for row in materials:
        row = dict(row)
        for channel in ("albedo", "normal", "roughness"):
            if row.get(channel):
                # re-rooted here for the same reason as in render.py: a moved run
                # must not read the textures of the run it was copied from
                row[channel] = str(out_dir / "materials" / Path(row[channel]).name)
        by_region[row["region_id"]] = row
    if weights is None:
        weights = np.load(out_dir / "layout_masks.npy")

    channels = ("albedo", "normal", "roughness")
    accumulated = {
        name: np.zeros((resolution, resolution, 3), np.float32) for name in channels
    }
    for index, row in enumerate(plan.layout):
        material = by_region[row.region_id]
        repeats = plan.world.size_m / max(material["scale_m"], 1e-3)
        weight = np.asarray(
            Image.fromarray((np.clip(weights[index], 0, 1) * 255).astype(np.uint8)).resize(
                (resolution, resolution), Image.BILINEAR
            ),
            np.float32,
        )[..., None] / 255.0
        for name in channels:
            accumulated[name] += _tiled(Path(material[name]), repeats, resolution) * weight

    written = {}
    for name, image in accumulated.items():
        total = sum(
            np.asarray(
                Image.fromarray((np.clip(weights[i], 0, 1) * 255).astype(np.uint8)).resize(
                    (resolution, resolution), Image.BILINEAR
                ),
                np.float32,
            )
            for i in range(len(plan.layout))
        )[..., None] / 255.0
        normalised = image / np.maximum(total, 1e-4)
        path = out_dir / f"terrain_{name}.png"
        Image.fromarray((np.clip(normalised, 0, 1) * 255).astype(np.uint8)).save(path)
        written[name] = str(path)
    return written


__all__ = ["bake", "pack_masks", "RESOLUTION"]
