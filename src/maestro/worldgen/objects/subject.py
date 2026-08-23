"""Redraw each found subject from its description rather than reconstruct its crop: a
shallow-angle composition crop comes back from TRELLIS2 as a flat card for foliage and
open lattices, a redrawn subject does not (measured, see docs/experiments.md).
"""
from __future__ import annotations

from pathlib import Path

from ..backends import ImageModel

_HERE = Path(__file__).parent
# Appended to whatever the vision model wrote: what makes an image reconstructable,
# not what the object is.
SUBJECT_STYLE = (_HERE / "subject_style.txt").read_text().strip()
SUBJECT_NEGATIVE = (_HERE / "subject_negative.txt").read_text().strip()

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
