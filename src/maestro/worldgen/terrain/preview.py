"""Looking at a terrain before there is an engine to look at it in.

The refinement loop renders the world and inspects it. Before any of
that exists there is still a question worth answering cheaply — did the height
field come out as the plan described? — and a hillshade answers it. Relief,
region boundaries, channels and scattered instances, in one image, from numpy.

This is a diagnostic, not the deliverable. It draws no materials and no meshes;
what it shows is the geometry and the partition, which is exactly what goes
wrong first.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from .layout import assign_colours
from .models import TerrainPlan


def hillshade(
    height: np.ndarray,
    size_m: float,
    *,
    azimuth_deg: float = 315.0,
    altitude_deg: float = 40.0,
) -> np.ndarray:
    """Classic hillshade in 0..1. Light from the north-west, as maps are lit."""
    metres_per_pixel = size_m / height.shape[0]
    dy, dx = np.gradient(height.astype(np.float32), metres_per_pixel)
    slope = np.arctan(np.sqrt(dx * dx + dy * dy))
    aspect = np.arctan2(-dy, dx)
    azimuth = np.deg2rad(360.0 - azimuth_deg + 90.0)
    altitude = np.deg2rad(altitude_deg)
    shade = np.sin(altitude) * np.cos(slope) + np.cos(altitude) * np.sin(slope) * np.cos(
        azimuth - aspect
    )
    return np.clip(shade, 0.0, 1.0)


def render(
    plan: TerrainPlan,
    height: np.ndarray,
    weights: np.ndarray,
    out: Path | str,
    *,
    instances: dict[str, list[dict]] | None = None,
    size: int = 1024,
) -> Path:
    """A hillshaded top-down preview, tinted by region and dotted with instances."""
    shade = hillshade(height, plan.world.size_m)

    colours = assign_colours(plan)
    palette = np.array(
        [colours[row.region_id] for row in plan.layout], np.float32
    ) / 255.0
    tint = np.tensordot(weights, palette, axes=(0, 0))  # (n, n, 3)

    # relief drives value, region drives hue: a flat region and a rough one look
    # different even where they are the same colour
    image = tint * (0.35 + 0.85 * shade[..., None])

    under_water = height < plan.world.sea_level_m
    if under_water.any():
        image[under_water] = np.array([0.16, 0.28, 0.42], np.float32)

    picture = Image.fromarray(
        (np.clip(image, 0.0, 1.0) * 255).astype(np.uint8)
    ).resize((size, size), Image.LANCZOS)

    if instances:
        draw = ImageDraw.Draw(picture)
        scale = size / plan.world.size_m
        for index, (category, group) in enumerate(sorted(instances.items())):
            shade_of = 40 + (index * 47) % 180
            for item in group:
                x, _, z = item["position"]
                radius = max(1.5, item["height_m"] * scale * 0.5)
                draw.ellipse(
                    [x * scale - radius, z * scale - radius,
                     x * scale + radius, z * scale + radius],
                    fill=(255, shade_of, 40), outline=None,
                )
    out = Path(out)
    picture.save(out)
    return out


def height_png(height: np.ndarray, out: Path | str) -> Path:
    """The raw height field as greyscale, normalised. For checking range, not looks."""
    low, high = float(height.min()), float(height.max())
    span = max(high - low, 1e-6)
    Image.fromarray((((height - low) / span) * 255).astype(np.uint8)).save(out)
    return Path(out)


__all__ = ["render", "hillshade", "height_png"]
