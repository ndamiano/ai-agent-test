"""Terrain asset prototypes: I_asset, then O_asset (§2.2.2).

"For environmental elements that are repeatedly instantiated across the global
terrain, this stage generates reusable 3D asset prototypes without determining
instance-specific positions, scales, or orientations."

Two steps, in that order. A reference image per category, then a mesh from each
reference. Where each instance goes, how big it is and which way it faces are
decided later, against a height field that does not exist yet.

The prototype is the category, not the instance: one "granite boulder" mesh is
scattered eight hundred times. That is what makes a world of this size possible
at all, and it is why the reference image has to show the object alone, whole,
and lit from nowhere in particular — anything baked into it is baked into every
copy.
"""
from __future__ import annotations

import contextvars
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from ..backends import ImageModel, MeshModel
from .models import TerrainAsset, TerrainPlan

# What an image-to-3D model needs and a scene photograph never gives it: the
# whole object, no crop, no companions, no ground it is standing on.
SUBJECT_TEMPLATE = (
    "{appearance} A single isolated {category}, alone on a plain white "
    "background, the entire object visible and centred, photographed from a "
    "three-quarter view slightly above, even diffuse light, sharp focus, "
    "no shadow on the ground, no other objects."
)
# Triangles per prototype, by how much structure the plan says the category has.
#
# These are per-instance budgets for things that scatter in the hundreds, so they
# are set against a whole-map cost rather than against how the mesh looks alone:
# at these numbers a typical world's scatter comes to a few million triangles
# instead of the ~47M that a flat 50k for everything produced.
#
# Intricate is not generous by accident. A tree decimated to undergrowth budget
# loses the branch silhouette that is the entire read of the object, while a
# boulder at 3k is indistinguishable from a boulder at 50k. Spend where shape
# carries meaning.
DETAIL_TRIANGLES = {
    "simple": 3_000,
    "moderate": 4_000,
    "intricate": 8_000,
}

SUBJECT_NEGATIVE = (
    "multiple objects, cropped, cut off, group, collection, scene, landscape, "
    "ground, horizon, people, hands, text, watermark, drop shadow, vignette, "
    "blurry, illustration, drawing"
)


def slug(category: str) -> str:
    """A filename for a category, stable across runs.

    Meshes are keyed by this, and `MeshModel` skips a reconstruction whose GLB
    already exists, so it has to be a pure function of the category name.
    """
    return "".join(c if c.isalnum() else "-" for c in category.lower()).strip("-")


def reference_images(
    plan: TerrainPlan,
    out_dir: Path | str,
    *,
    images: ImageModel | None = None,
    size: int = 1024,
    seed: int = 3,
    overwrite: bool = False,
) -> dict[str, Path]:
    """I_asset: one reference image per terrain asset category.

    Cut out to alpha, because the mesh model reconstructs what is opaque and a
    white background reconstructs as a white wall behind the object.

    An image already on disk is kept unless `overwrite`, matching how meshes
    behave. A stage that dies in its ninth reconstruction should resume, not
    start over.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    images = images or ImageModel()
    made: dict[str, Path] = {}
    pending = []
    for index, asset in enumerate(plan.assets):
        path = out_dir / f"{slug(asset.category)}.png"
        if path.exists() and not overwrite:
            made[asset.category] = path
            continue
        pending.append((asset, path, seed + index * 101))

    def _one(asset, path, one_seed) -> None:
        images.generate(
            SUBJECT_TEMPLATE.format(appearance=asset.appearance, category=asset.category),
            path,
            negative=SUBJECT_NEGATIVE,
            width=size,
            height=size,
            # one seed per category rather than per run: re-running a stage
            # should not silently replace prototypes that were already fine
            seed=one_seed,
            cutout=True,
        )

    with ThreadPoolExecutor(max_workers=min(16, len(pending) or 1)) as pool:
        futures = {
            pool.submit(contextvars.copy_context().run, _one, asset, path, one_seed): asset
            for asset, path, one_seed in pending
        }
        for future, asset in futures.items():
            future.result()
            made[asset.category] = out_dir / f"{slug(asset.category)}.png"
    return made


def prototypes(
    plan: TerrainPlan,
    out_dir: Path | str,
    *,
    references: dict[str, Path] | None = None,
    meshes: MeshModel | None = None,
) -> dict[str, Path | None]:
    """O_asset: a mesh per category, reconstructed from its reference image.

    Returns category -> GLB, with None for any that failed. A category without a
    mesh is one category that will not scatter; it is not a failed stage.
    """
    out_dir = Path(out_dir)
    references = references or {
        asset.category: out_dir / f"{slug(asset.category)}.png" for asset in plan.assets
    }
    wanted = [references[a.category] for a in plan.assets if a.category in references]
    if not wanted:
        return {}
    meshes = meshes or MeshModel()
    # keyed by image stem, because that is what `reconstruct` matches on
    targets = {
        slug(asset.category): DETAIL_TRIANGLES[asset.detail]
        for asset in plan.assets
        if asset.category in references
    }
    made = meshes.reconstruct(wanted, out_dir / "meshes", targets=targets)
    return {
        asset.category: made.get(str(references[asset.category]))
        for asset in plan.assets
        if asset.category in references
    }


def write_manifest(
    plan: TerrainPlan,
    out_dir: Path | str,
    references: dict[str, Path],
    glbs: dict[str, Path | None],
) -> Path:
    """Record what exists, with the scattering rules each prototype carries.

    The scattering stage should not have to re-read the terrain plan and join it
    against a directory listing; this is that join, done once.
    """
    out_dir = Path(out_dir)
    rows = []
    for asset in plan.assets:
        glb = glbs.get(asset.category)
        rows.append({
            "category": asset.category,
            "image": str(references.get(asset.category, "")) or None,
            "mesh": str(glb) if glb else None,
            "height_m": asset.height_m,
            "regions": asset.regions,
            "per_hectare": asset.per_hectare,
            "max_slope_deg": asset.max_slope_deg,
        })
    path = out_dir / "prototypes.json"
    path.write_text(json.dumps(rows, indent=2))
    return path


__all__ = ["reference_images", "prototypes", "write_manifest", "slug"]
