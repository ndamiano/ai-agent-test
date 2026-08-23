"""M_terrain: the surfaces the regions are covered in.

Synthesises a PAIR of albedos per region from p_material — the surface and the
variant it wears through to — then derives a normal map from each.

Derived, not generated, and worth being plain about. A diffusion model asked for
a normal map returns a picture of one — plausible pastel blue, uncorrelated with
the albedo it is supposed to accompany. Deriving the normal from the albedo's
own luminance gradient at least guarantees the bumps line up with the grains,
which is the property that matters at grazing light.

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
from PIL import Image

from tools.quilting import quilt_tile

from ..backends import ImageModel, ImageModelError
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
_HERE = Path(__file__).parent
TEXTURE_TEMPLATE = (_HERE / "texture_prompt.txt").read_text().strip()
TEXTURE_NEGATIVE = (_HERE / "texture_negative.txt").read_text().strip()

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
            try:
                future.result()
            except ImageModelError:
                pass  # a refused render is one missing material; the stage goes on
    return made


def derive_normal(albedo_png: Path | str, strength: float = 2.5) -> Path:
    """Write a normal map beside an albedo and return its path.

    From the luminance gradient: treat brightness as height, take its slope in x
    and y, and normalise. It is not a measurement of the real surface — no image
    is — but its bumps are the albedo's own bumps, which is what stops lighting
    from disagreeing with the picture.
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
    return normal_path


def build(
    plan: TerrainPlan,
    out_dir: Path | str,
    *,
    images: ImageModel | None = None,
    size: int = SIZE,
) -> Path:
    """Generate every region's material and write the manifest. Returns its path."""
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
        }
        for role, prefix in (("base", ""), ("variant", "variant_")):
            path = pair[role]
            if not path.exists():
                continue  # a render the safety screen refused; the base alone still draws
            row[f"{prefix}albedo"] = str(path)
            row[f"{prefix}normal"] = str(derive_normal(path))
        rows.append(row)
    manifest = out_dir / "materials.json"
    manifest.write_text(json.dumps(rows, indent=2))
    return manifest


__all__ = ["albedo", "derive_normal", "build", "TEXTURE_TEMPLATE"]
