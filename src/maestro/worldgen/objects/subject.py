"""Turning an instance in a composition into something reconstructable.

TRELLIS2 is handed a crop of the region composition, and for a whole class of
subject that crop cannot be reconstructed from. Measured on the jungle town:
foliage and open lattice structures come back as flat cards at any source
resolution, while closed opaque volumes reconstruct fine at 76 pixels. Size was
never the discriminator -- a 331 px fence failed and a 76 px barrel succeeded.

What the crop actually gives the model is a subject at a shallow near-orthographic
angle, blurred by upscaling, cut out by a segmentation mask with holes in it, and
sharing the frame with whatever ground and neighbours fell inside its box.

So the subject is re-drawn rather than re-cut. A vision model looks at the crop
and writes down what the thing is; a text-to-image model draws that from nothing,
whole and isolated and lit, at full resolution and from an angle that shows its
depth; and that is what gets reconstructed.

The cost is honest and worth stating: this is no longer the same object the
composition drew. It is an object of the same kind, described from it. Position
and size still come from the instance mask and are unaffected, so what drifts is
identity, not placement.
"""
from __future__ import annotations

import base64
from pathlib import Path

from ..backends import ImageModel

DEFAULT_MODEL = "qwen3.8_27b"

# Appended to whatever the vision model wrote. This half is about what makes an
# image reconstructable rather than about what the object is: one subject, whole,
# lit from several directions so its form reads, and seen from an angle that has
# depth in it. The crops fail partly because a shallow camera gives almost no
# parallax to infer thickness from.
SUBJECT_STYLE = (
    "Single object, complete and unobstructed, centred and filling the frame. "
    "Three-quarter view from slightly above, showing its depth and thickness. "
    "Plain flat mid-grey background. Soft even studio lighting from several "
    "directions. Sharp focus, high detail, photographic."
)

SUBJECT_NEGATIVE = (
    "blurry, low detail, cropped, cut off, partial object, multiple objects, "
    "scenery, landscape, ground, floor, horizon, cast shadow, text, watermark, "
    "flat, front view, orthographic"
)


def _image_part(path: Path) -> dict:
    data = base64.b64encode(Path(path).read_bytes()).decode()
    return {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{data}"}}


# How far the drawing canvas may depart from square. A canvas shaped like the
# object is the whole point; a canvas shaped like a letterbox gives the model
# too little to draw the object's depth into.
MIN_ASPECT = 0.28
MAX_ASPECT = 2.2
PIXELS = 1024 * 1024


def draw(
    prompt: str,
    out: Path | str,
    *,
    images: ImageModel | None = None,
    seed: int = 0,
    aspect: float | None = None,
    size: int = 1024,
    steps: int = 28,
) -> Path:
    """Draw one isolated subject on alpha, ready for reconstruction.

    `aspect` is height over width, normally taken from the instance's own mask.
    It matters more than it sounds: told to fill a SQUARE frame with a fence, an
    image model draws a short tall gate panel, and the reconstruction is then
    faithfully a gate panel. Measured on this world, a fence whose mask is three
    times wider than tall came back as a mesh 0.87 as tall as wide; drawn on a
    canvas of its own shape it comes back at 0.37, which is a fence rather than
    a wall. Objects that are roughly square anyway -- barrels, trees, houses --
    are unaffected, which is why the defect hid for so long.
    """
    images = images or ImageModel()
    if aspect is None:
        width = height = size
    else:
        ratio = float(min(max(aspect, MIN_ASPECT), MAX_ASPECT))
        width = int(round((PIXELS / ratio) ** 0.5))
        height = int(round(width * ratio))
    return images.generate(
        f"{prompt} {SUBJECT_STYLE}",
        out,
        negative=SUBJECT_NEGATIVE,
        width=width, height=height,
        seed=seed, steps=steps,
        cutout=True,
    )


__all__ = [
    "draw",
    "SUBJECT_STYLE",
]
