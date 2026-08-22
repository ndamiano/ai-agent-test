"""M_terrain: the surfaces the regions are covered in (§2.2.2).

The paper builds materials two ways. "The generative pathway produces texture
channels such as albedo, normal, and roughness maps for local surfaces with
complex appearance or irregular details. The procedural pathway programmatically
assembles Blender material nodes to create tileable and parameter-adjustable
surface materials for large-scale regions."

This module is the generative pathway. It synthesises an albedo per region from
p_material, then derives normal and roughness from it.

Derived, not generated, and worth being plain about. A diffusion model asked for
a normal map returns a picture of one — plausible pastel blue, uncorrelated with
the albedo it is supposed to accompany. Deriving the normal from the albedo's
own luminance gradient at least guarantees the bumps line up with the grains,
which is the property that matters at grazing light. Roughness comes from local
contrast: polished surfaces are locally smooth, weathered ones are not.

The procedural pathway belongs with the renderer — it writes shader code, and
what that code has to compile against is the terrain shader. It lives in the
terrain construction stage, not here.

Every material is written with the metre width of one repeat beside it, because
a texture without a scale is the single most visible error in a finished
terrain, and nothing downstream can recover it from the image.
"""
from __future__ import annotations

import contextvars
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

from ..backends import ImageModel
from .models import TerrainPlan

# A tiling texture must be homogeneous, and saying so is not optional. Asked for
# a battlefield sand "covering about 10 metres", the model returns a genuinely
# good photograph of sand — containing two craters and a set of tyre tracks. At
# 10 m across a 400 m world those craters recur forty times in each direction,
# and what the terrain shows is not a subtle tiling seam but a regular lattice of
# identical craters. The features have to be absent from the texture; the terrain
# gets its craters from the height field, where they belong.
# Homogeneous is not enough on its own. A cobbled street or a plank floor is
# uniform and still tiles visibly, because its elements are large: a texture with
# eight slabs across it shows those eight slabs everywhere, and the eye finds the
# repeat immediately however the sampling is randomised. Elements have to be
# small relative to the frame, so that what recurs is a grain rather than a
# recognisable shape.
TEXTURE_TEMPLATE = (
    "A seamless tileable material texture of {surface}, photographed from "
    "directly overhead at close range. {appearance} Completely uniform and "
    "homogeneous across the whole frame: the same fine surface detail everywhere, "
    "no large features, no focal point, no single object anywhere in the frame. "
    "Any repeating elements — stones, planks, tiles, grains — must be small and "
    "numerous, dozens across the frame, none of them large enough to be picked "
    "out on its own. Flat even overcast light, no shadows, no horizon, sharp "
    "detail throughout."
)
TEXTURE_NEGATIVE = (
    "perspective, horizon, sky, objects, people, text, watermark, vignette, "
    "strong shadows, directional light, blurry, seams, border, frame, "
    "illustration, "
    # the features that turn a texture into a repeating landmark
    "craters, holes, pits, tracks, tyre tracks, footprints, ruts, trails, paths, "
    "boulders, large rocks, debris, wreckage, plants, bushes, focal point, "
    "composition, landmark, distinct features, uneven detail, "
    # elements big enough to be recognised are what makes a repeat visible
    "large slabs, large tiles, large stones, large planks, few elements, "
    "sparse pattern, regular grid, checkerboard"
)

SIZE = 1536  # more grain per repeat, so a tile carries detail rather than shapes


def albedo(
    plan: TerrainPlan,
    out_dir: Path | str,
    *,
    images: ImageModel | None = None,
    size: int = SIZE,
    seed: int = 23,
    overwrite: bool = False,
) -> dict[str, Path]:
    """One albedo per region, at the scale its material says it repeats at.

    Kept if already on disk, like the asset references and the meshes.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    images = images or ImageModel()
    made: dict[str, Path] = {}
    pending = []
    for index, material in enumerate(plan.materials):
        path = out_dir / f"{material.region_id}_albedo.png"
        if path.exists() and not overwrite:
            made[material.region_id] = path
            continue
        pending.append((material, path, seed + index * 37))

    def _one(material, path, one_seed) -> None:
        images.generate(
            TEXTURE_TEMPLATE.format(
                surface=material.surface,
                appearance=material.appearance,
            ),
            path,
            negative=TEXTURE_NEGATIVE,
            width=size,
            height=size,
            seed=one_seed,
        )

    with ThreadPoolExecutor(max_workers=min(16, len(pending) or 1)) as pool:
        futures = {
            pool.submit(contextvars.copy_context().run, _one, material, path, one_seed): material
            for material, path, one_seed in pending
        }
        for future, material in futures.items():
            future.result()
            made[material.region_id] = out_dir / f"{material.region_id}_albedo.png"
    return made


def derive_channels(albedo_png: Path | str, strength: float = 2.5) -> tuple[Path, Path]:
    """Write a normal and a roughness map beside an albedo. Returns both paths.

    The normal comes from the luminance gradient: treat brightness as height,
    take its slope in x and y, and normalise. It is not a measurement of the
    real surface — no image is — but its bumps are the albedo's own bumps, which
    is what stops lighting from disagreeing with the picture.

    Roughness comes from local contrast: the difference between the luminance
    and a blurred copy of it. A surface with fine structure everywhere reads
    rough; a smooth one reads polished. Mapped into a middling band rather than
    the full range, because a fully smooth or fully rough ground is a mirror or
    a chalkboard and neither exists outdoors.
    """
    albedo_png = Path(albedo_png)
    image = Image.open(albedo_png).convert("RGB")
    pixels = np.asarray(image, np.float32) / 255.0
    luminance = pixels @ np.array([0.2126, 0.7152, 0.0722], np.float32)

    dy, dx = np.gradient(luminance)
    normal = np.stack([-dx * strength, dy * strength, np.ones_like(luminance)], -1)
    normal /= np.linalg.norm(normal, axis=-1, keepdims=True)
    normal_path = albedo_png.with_name(albedo_png.stem.replace("_albedo", "") + "_normal.png")
    Image.fromarray(((normal * 0.5 + 0.5) * 255).astype(np.uint8)).save(normal_path)

    blurred = np.asarray(
        Image.fromarray((luminance * 255).astype(np.uint8)).filter(
            ImageFilter.GaussianBlur(4)
        ),
        np.float32,
    ) / 255.0
    contrast = np.abs(luminance - blurred)
    spread = float(contrast.max()) or 1.0
    roughness = 0.55 + 0.40 * np.clip(contrast / spread, 0.0, 1.0)
    roughness_path = albedo_png.with_name(
        albedo_png.stem.replace("_albedo", "") + "_roughness.png"
    )
    Image.fromarray((roughness * 255).astype(np.uint8)).save(roughness_path)
    return normal_path, roughness_path


def build(
    plan: TerrainPlan,
    out_dir: Path | str,
    *,
    images: ImageModel | None = None,
    size: int = SIZE,
) -> Path:
    """Generate every region's material and write the manifest. Returns its path.

    Regions whose plan asked for the procedural pathway still get an albedo
    here. Their shader is written later against the engine, and until it is, a
    generated texture is a better placeholder than an untextured surface — the
    manifest records which pathway each region actually asked for, so that stage
    knows which ones it owns.
    """
    out_dir = Path(out_dir)
    albedos = albedo(plan, out_dir, images=images, size=size)
    rows = []
    for material in plan.materials:
        path = albedos[material.region_id]
        normal, roughness = derive_channels(path)
        rows.append({
            "region_id": material.region_id,
            "surface": material.surface,
            "appearance": material.appearance,
            "scale_m": material.scale_m,
            "pathway": material.pathway,
            "albedo": str(path),
            "normal": str(normal),
            "roughness": str(roughness),
        })
    manifest = out_dir / "materials.json"
    manifest.write_text(json.dumps(rows, indent=2))
    return manifest


__all__ = ["albedo", "derive_channels", "build", "TEXTURE_TEMPLATE"]
