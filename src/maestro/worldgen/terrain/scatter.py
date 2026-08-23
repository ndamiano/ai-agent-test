"""Global terrain asset scattering.

"For each terrain-asset category, the agent samples candidate locations within
the corresponding layout masks according to the regional affinities and target
densities in p_asset. Local elevation, slope, and surface normals are then used
to filter or adjust the sampled instances."

Positions come out of the same region weights the height field was built from,
so a rock that belongs to the ridge is on the ridge as the ridge was actually
drawn, not as it was planned. Height and orientation come out of the height
field itself, which is what stops instances floating or sinking.

What this does not do is decide what the objects are or how they relate to each
other. These are the repeated furniture of the ground — rocks, scrub, debris —
placed by density and slope. Anything with an identity is a later stage's.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from ..seed import seed_for
from .heightfield import normals, slope_deg
from .models import TerrainAsset, TerrainPlan

HECTARE_M2 = 10_000.0

# Minimum separation as a fraction of the instance's own footprint. Without it,
# density sampling clumps: independent draws from the same distribution land on
# top of each other often enough to read as a pile.
SPACING = 0.75


def _region_index(plan: TerrainPlan, region_id: str) -> int:
    return plan.region_ids().index(region_id)


def candidate_field(
    plan: TerrainPlan,
    asset: TerrainAsset,
    weights: np.ndarray,
    slope: np.ndarray,
    height: np.ndarray,
) -> np.ndarray:
    """Where this asset may go, as a per-pixel weight.

    The region weights say where it belongs; slope and sea level say where it
    can physically be.
    """
    affinity = np.zeros_like(slope)
    for region_id in asset.regions:
        affinity = np.maximum(affinity, weights[_region_index(plan, region_id)])
    # a soft slope cutoff rather than a hard one: an asset does not become
    # impossible one degree past its limit, it becomes unlikely
    steepness = np.clip((asset.max_slope_deg - slope) / 8.0, 0.0, 1.0)
    field = affinity * steepness
    field = np.where(height > plan.world.sea_level_m, field, 0.0)
    return field


def place(
    plan: TerrainPlan,
    asset: TerrainAsset,
    weights: np.ndarray,
    height: np.ndarray,
    *,
    seed: int | None = None,
) -> list[dict]:
    """Instances of one asset category: position, height, yaw, scale.

    The count comes from the target density and the area the asset actually has
    available, not the area its regions nominally cover — a category confined to
    slopes gentler than 20 degrees in a region that is mostly cliff should end up
    with few instances, and it does.
    """
    resolution = height.shape[0]
    size_m = plan.world.size_m
    metres_per_pixel = size_m / resolution

    slope = slope_deg(height, size_m)
    field = candidate_field(plan, asset, weights, slope, height)
    available_m2 = float(field.sum()) * metres_per_pixel ** 2
    count = int(round(available_m2 / HECTARE_M2 * asset.per_hectare))
    if count <= 0 or field.sum() <= 0:
        return []

    generator = np.random.default_rng(
        seed if seed is not None else seed_for(asset.category)
    )
    probability = field.reshape(-1) / field.sum()
    # oversample, then thin by spacing: rejecting after the fact is cheaper than
    # a true Poisson-disc process and the distribution is indistinguishable once
    # the minimum distance is this small relative to the world
    draws = generator.choice(
        probability.size, size=min(count * 3, probability.size), p=probability, replace=False
    )
    rows, cols = np.divmod(draws, resolution)

    surface = normals(height, size_m)
    minimum_gap = max(asset.height_m * SPACING, metres_per_pixel)
    taken: list[tuple[float, float]] = []
    instances: list[dict] = []
    for row, col in zip(rows, cols):
        if len(instances) >= count:
            break
        x = (col + 0.5) * metres_per_pixel
        z = (row + 0.5) * metres_per_pixel
        if any(
            (x - px) ** 2 + (z - pz) ** 2 < minimum_gap ** 2 for px, pz in taken[-400:]
        ):
            continue
        taken.append((x, z))
        normal = surface[row, col]
        instances.append({
            "category": asset.category,
            # metres from the north-west corner: x east, z south, y up
            "position": [round(x, 3), round(float(height[row, col]), 3), round(z, 3)],
            "normal": [round(float(v), 4) for v in normal],
            "yaw_deg": round(float(generator.uniform(0.0, 360.0)), 1),
            # size varies; a field of identical rocks reads as instanced geometry
            "scale": round(float(np.clip(generator.normal(1.0, 0.18), 0.55, 1.7)), 3),
            "height_m": asset.height_m,
            "slope_deg": round(float(slope[row, col]), 2),
        })
    return instances


def scatter(
    plan: TerrainPlan,
    weights: np.ndarray,
    height: np.ndarray,
    out_dir: Path | str,
) -> dict[str, list[dict]]:
    """Place every terrain asset and write `scatter.json`. Returns the instances."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    placed = {
        asset.category: place(plan, asset, weights, height)
        for asset in plan.assets
    }
    (out_dir / "scatter.json").write_text(
        json.dumps(
            {
                "world_size_m": plan.world.size_m,
                "counts": {k: len(v) for k, v in placed.items()},
                "instances": [i for group in placed.values() for i in group],
            },
            indent=1,
        )
    )
    return placed


__all__ = ["scatter", "place", "candidate_field"]
