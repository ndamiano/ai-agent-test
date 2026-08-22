"""Terrain generation: from the terrain plan to T.

    T = (geometry, regional semantics, surface materials, scattered assets)

The height field is built from the plan's numbers and the masks read out of the
layout map, the assets are scattered on it, and the result is written out as
data: a height array in metres, the shared region weights, an instance list,
and the material assignment.

Data, not a scene file, and that is the whole design. Nothing here opens an
engine. The refinement loop edits terrain parameters and rebuilds — which is
only possible if rebuilding is a pure function of the plan — and the engine
reads the result. A pipeline that edited geometry directly could not be
re-derived from its own specification.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from . import preview as preview_module
from . import scatter as scatter_module
from .heightfield import build, slope_deg
from .models import TerrainPlan


def construct(
    plan: TerrainPlan,
    out_dir: Path | str,
    *,
    weights: np.ndarray | None = None,
) -> dict:
    """Build T into `out_dir` and return a summary of it.

    `weights` defaults to the masks stage 2b read out of the layout map. Passing
    them explicitly is for the refinement loop, which rebuilds the geometry over
    and over against a partition that has not changed.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    if weights is None:
        weights = np.load(out_dir / "layout_masks.npy")

    height = build(plan, weights)
    instances = scatter_module.scatter(plan, weights, height, out_dir)

    np.save(out_dir / "heightmap.npy", height)
    preview_module.height_png(height, out_dir / "heightmap.png")
    preview_module.render(
        plan, height, weights, out_dir / "terrain_preview.png",
        instances=instances,
    )

    slope = slope_deg(height, plan.world.size_m)
    summary = {
        "world_size_m": plan.world.size_m,
        "resolution": int(height.shape[0]),
        "metres_per_pixel": round(plan.world.size_m / height.shape[0], 4),
        "elevation_m": {
            "min": round(float(height.min()), 2),
            "max": round(float(height.max()), 2),
            "mean": round(float(height.mean()), 2),
        },
        "slope_deg": {
            "median": round(float(np.median(slope)), 2),
            "p95": round(float(np.percentile(slope, 95)), 2),
            "max": round(float(slope.max()), 2),
        },
        "regions": {
            row.region_id: {
                "category": row.category,
                "share": round(float(weights[index].mean()), 4),
                "mean_elevation_m": round(
                    float((height * weights[index]).sum() / max(weights[index].sum(), 1e-6)), 2
                ),
            }
            for index, row in enumerate(plan.layout)
        },
        "scatter": {k: len(v) for k, v in instances.items()},
        "files": {
            "heightmap": str(out_dir / "heightmap.npy"),
            "masks": str(out_dir / "layout_masks.npy"),
            "scatter": str(out_dir / "scatter.json"),
            "preview": str(out_dir / "terrain_preview.png"),
            "materials": str(out_dir / "materials" / "materials.json"),
            "prototypes": str(out_dir / "prototypes.json"),
        },
    }
    (out_dir / "terrain.json").write_text(json.dumps(summary, indent=2))
    return summary


__all__ = ["construct"]
