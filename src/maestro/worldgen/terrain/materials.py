"""M_terrain: the surfaces the regions are covered in.

Materials are built two ways. "The generative pathway produces texture
channels such as albedo, normal, and roughness maps for local surfaces with
complex appearance or irregular details. The procedural pathway programmatically
assembles Blender material nodes to create tileable and parameter-adjustable
surface materials for large-scale regions."

This module is the generative pathway. It synthesises a PAIR of albedos per
region from p_material — the surface and the variant it wears through to — then
derives normal and roughness from each.

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

from tools.quilting import quilt_tile

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
# The failure the first two paragraphs do not cover is the picture that is not a
# surface at all: asked for a lakebed the model draws a lake — water, a far bank,
# a sky above it — and that photograph is then repeated over the ground as a
# lattice of little lakes. What is being asked for is the material a square metre
# of ground is made of, seen from a camera pointing straight down at it from
# knee height, with nothing else in the world. That has to be the first thing
# said and the last, because it is the one the prose is most likely to lose.
TEXTURE_TEMPLATE = (
    "A square of {surface}, seen from directly above, filling the whole frame. "
    "{appearance} This is the ground itself at close range, one or two metres "
    "across, as if a camera were held at knee height pointing straight down at "
    "it: no horizon, no sky, no water's edge, no view of a place, nothing "
    "standing on it and nothing to look at in it. Completely uniform and "
    "homogeneous across the whole frame: the same fine surface detail "
    "everywhere, no large features, no focal point, no single object anywhere. "
    "Any repeating elements — stones, grains, blades, cracks — must be small and "
    "numerous, dozens across the frame, none of them large enough to be picked "
    "out on its own. Flat even overcast light, no shadows, sharp detail "
    "throughout, seamless and tileable."
)
TEXTURE_NEGATIVE = (
    "perspective, horizon, sky, clouds, distance, landscape, scenery, aerial "
    "view, shoreline, water's edge, far bank, objects, people, text, watermark, "
    "vignette, strong shadows, directional light, blurry, seams, border, frame, "
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
# What the quilt lays down. The render is an exemplar, not a tile: its edges do
# not meet themselves, and a ground drawn from it shows a hard seam every repeat
# however the sampling is randomised. `quilt_tile` synthesises a torus from the
# render's middle, so the tile is seamless by construction rather than by luck.
TILE = 768


def _texture(images: ImageModel, prompt: str, appearance: str, path: Path,
             size: int, seed: int) -> None:
    """One square of ground: rendered, then quilted into something that tiles."""
    images.generate(
        TEXTURE_TEMPLATE.format(surface=prompt, appearance=appearance),
        path,
        negative=TEXTURE_NEGATIVE,
        width=size,
        height=size,
        seed=seed,
    )
    if not path.exists():
        return  # a refused render is one missing material, as everywhere else
    quilt_tile(Image.open(path), out_size=TILE, block=TILE // 4, overlap=TILE // 16,
               seed=seed).save(path)


def albedo(
    plan: TerrainPlan,
    out_dir: Path | str,
    *,
    images: ImageModel | None = None,
    size: int = SIZE,
    seed: int = 23,
    overwrite: bool = False,
) -> dict[str, dict[str, Path]]:
    """The pair of squares each region is covered in: its base and its variant.

    Two, because one is a lie about ground. A single tile laid over a region is
    the same square metre everywhere in it however cleverly it is sampled, and
    what gives it away is not the seam but the sameness: real ground wears
    through, and where it wears is where the slope is and where the low-frequency
    patches say so. Both come back keyed 'base' and 'variant'.

    Kept if already on disk, like the asset references and the meshes.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    images = images or ImageModel()
    made: dict[str, dict[str, Path]] = {}
    pending = []
    for index, material in enumerate(plan.materials):
        paths = {
            "base": out_dir / f"{material.region_id}_albedo.png",
            "variant": out_dir / f"{material.region_id}_variant_albedo.png",
        }
        made[material.region_id] = paths
        for offset, (role, path) in enumerate(paths.items()):
            if path.exists() and not overwrite:
                continue
            pending.append((
                material.variant if role == "variant" else material.surface,
                "" if role == "variant" else material.appearance,
                path,
                seed + index * 37 + offset * 11,
            ))

    def _one(prompt, appearance, path, one_seed) -> None:
        _texture(images, prompt, appearance, path, size, one_seed)

    with ThreadPoolExecutor(max_workers=min(16, len(pending) or 1)) as pool:
        futures = [
            pool.submit(contextvars.copy_context().run, _one, *job) for job in pending
        ]
        for future in futures:
            future.result()
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
        pair = albedos[material.region_id]
        row = {
            "region_id": material.region_id,
            "surface": material.surface,
            "appearance": material.appearance,
            "variant": material.variant,
            "scale_m": material.scale_m,
            "pathway": material.pathway,
        }
        for role, prefix in (("base", ""), ("variant", "variant_")):
            path = pair[role]
            if not path.exists():
                continue  # a render the safety screen refused; the base alone still draws
            normal, roughness = derive_channels(path)
            row[f"{prefix}albedo"] = str(path)
            row[f"{prefix}normal"] = str(normal)
            row[f"{prefix}roughness"] = str(roughness)
        rows.append(row)
    manifest = out_dir / "materials.json"
    manifest.write_text(json.dumps(rows, indent=2))
    return manifest


__all__ = ["albedo", "derive_channels", "build", "TEXTURE_TEMPLATE"]
