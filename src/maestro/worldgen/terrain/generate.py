"""Stage 2b — terrain asset generation.

    A_terrain = (I_layout, I_asset, O_asset, M_terrain)

The layout map and the region masks, a reference image and a mesh per scattered
category, and a material per region. Everything terrain construction needs in
order to build geometry, and nothing it has to invent.

The layout redraw, the asset references and the material albedos are each their
own job on the image queue; the mesh queue then reconstructs every prototype.
Mask readback and the derived material channels are pure numpy and run entirely
between the two.
"""
from __future__ import annotations

import json
import time
from pathlib import Path


from ..backends import ImageModel, MeshModel
from . import assets as assets_module
from . import layout as layout_module
from . import materials as materials_module
from .models import TerrainPlan


def generate_terrain_assets(
    plan: TerrainPlan,
    out_dir: Path | str,
    *,
    images: ImageModel | None = None,
    meshes: MeshModel | None = None,
) -> dict:
    """Build A_terrain into `out_dir`. Returns a summary of what was made."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    images = images or ImageModel()
    started = time.time()

    print("[terrain] drawing the layout map", flush=True)
    layout_module.draw(plan, out_dir, images=images)

    print(f"[terrain] {len(plan.assets)} asset references", flush=True)
    references = assets_module.reference_images(
        plan, out_dir / "subjects", images=images
    )

    print(f"[terrain] {len(plan.materials)} region materials", flush=True)
    materials_module.build(plan, out_dir / "materials", images=images)

    print("[terrain] reading region masks back out of the layout map", flush=True)
    weights = layout_module.read_back(plan, out_dir)

    glbs: dict[str, Path | None] = {}
    if plan.assets:
        print(f"[terrain] reconstructing {len(references)} prototypes", flush=True)
        glbs = assets_module.prototypes(
            plan, out_dir / "subjects", references=references, meshes=meshes or MeshModel()
        )
    assets_module.write_manifest(plan, out_dir, references, glbs)

    summary = {
        "seconds": round(time.time() - started, 1),
        "layout": str(out_dir / "layout.png"),
        "masks": str(out_dir / "layout_masks.npy"),
        "mask_shape": list(weights.shape),
        "references": {k: str(v) for k, v in references.items()},
        "meshes": {k: (str(v) if v else None) for k, v in glbs.items()},
        "materials": str(out_dir / "materials" / "materials.json"),
    }
    (out_dir / "terrain_assets.json").write_text(json.dumps(summary, indent=2))
    return summary


__all__ = ["generate_terrain_assets"]
