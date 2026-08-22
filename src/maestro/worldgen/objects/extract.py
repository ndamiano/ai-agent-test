"""Object generation, first half: instances out of the composition.

The objects are located by `ground.locate`, which asks a vision model where
everything is. This module turns each box into an object-centric crop the rest
of the stage works from, with the affine that produced it and the equivalent
intrinsics recorded alongside. Cropping and enlarging changes only the image
coordinate system, so the extrinsics are untouched and a pixel in the crop maps
back to the composition through the affine's inverse. That is what lets a small
object be reconstructed at high resolution without giving up any placement
accuracy.

There is no mask. Nothing downstream needs a silhouette: reconstruction stopped
using the cutout when subjects began being redrawn from a description rather
than cut out (see subject.py), and placement anchors on the bottom of the box,
which sits within 0.28 m of the bottom of a mask at these framings -- measured
over every instance of the cliff city. What a mask would buy is a tighter box
on large irregular objects, and it would cost a second model in the pipeline
for it.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image

from .camera import Camera
from .ground import Found

CROP_PX = 1024    # the object-centric image handed to reconstruction
CROP_PAD = 0.18   # of the instance's longest side, so it is not cut at the edge


@dataclass
class Instance:
    """One located object, in composition pixels."""

    category: str
    bbox: tuple[int, int, int, int]
    typical_size_m: float | None = None
    prompt: str | None = None

    @property
    def longest_px(self) -> int:
        return max(self.bbox[2] - self.bbox[0], self.bbox[3] - self.bbox[1])

    @property
    def centre(self) -> tuple[float, float]:
        x0, y0, x1, y1 = self.bbox
        return ((x0 + x1) * 0.5, (y0 + y1) * 0.5)

    @property
    def area(self) -> int:
        x0, y0, x1, y1 = self.bbox
        return max(x1 - x0, 0) * max(y1 - y0, 0)


def instances_from(found: list[Found]) -> list[Instance]:
    """What the grounding pass found, in the shape the rest of the stage wants."""
    return [
        Instance(item.label, item.bbox, item.size_m, item.prompt)
        for item in found
    ]


def crops(
    instances: list[Instance],
    composition: Path | str,
    camera: Camera,
    out_dir: Path | str,
    *,
    crop_px: int = CROP_PX,
    pad: float = CROP_PAD,
) -> list[dict]:
    """Write the object-centric crop for each instance and record its affine and intrinsics.

    The crop is square and the object is centred in it. Both are on purpose: an
    object sitting in the middle of a square reconstructs better than the same
    object against an edge, and a square is what reconstruction takes.
    """
    out_dir = Path(out_dir)
    (out_dir / "instances").mkdir(parents=True, exist_ok=True)
    source = Image.open(composition).convert("RGB")
    rows = []
    for index, instance in enumerate(instances):
        x0, y0, x1, y1 = instance.bbox
        cx, cy = (x0 + x1) * 0.5, (y0 + y1) * 0.5
        half = max(x1 - x0, y1 - y0) * (0.5 + pad)
        left, top = cx - half, cy - half
        side = half * 2.0
        scale = crop_px / side

        # composition coordinates -> object-centric image coordinates
        affine = np.array([
            [scale, 0.0, -scale * left],
            [0.0, scale, -scale * top],
            [0.0, 0.0, 1.0],
        ])

        box = (int(round(left)), int(round(top)),
               int(round(left + side)), int(round(top + side)))
        colour = source.crop(box).resize((crop_px, crop_px), Image.LANCZOS)

        stem = f"{index:03d}_{_slug(instance.category)}"
        image_path = out_dir / "instances" / f"{stem}.png"
        colour.save(image_path)

        rows.append({
            "index": index,
            "category": instance.category,
            "typical_size_m": instance.typical_size_m,
            "prompt": instance.prompt,
            "image": str(image_path),
            "bbox": [int(x0), int(y0), int(x1), int(y1)],
            "centre_px": list(instance.centre),
            "area_px": instance.area,
            "longest_px": instance.longest_px,
            "affine": affine.tolist(),
            "intrinsics": (affine @ camera.intrinsics).tolist(),
        })
    return rows


def _slug(text: str) -> str:
    return "".join(c if c.isalnum() else "-" for c in text.lower()).strip("-")


def extract(
    composition: Path | str,
    camera: Camera,
    out_dir: Path | str,
    *,
    sizes: dict[str, float] | None = None,
    verbose: bool = True,
) -> list[dict]:
    """Locate the objects, crop them, and write `instances.json`.

    `sizes` is the regional plan's stated size per category, which wins over the
    vision model's estimate wherever a category was planned: the plan's number is
    a design decision and stable, while the estimate moved between 15 m and 30 m
    for the same temple in two consecutive calls. Objects nobody planned -- and
    the grounding pass finds plenty -- keep the estimate, since some number is
    needed and it is the only one there is.
    """
    from . import ground as ground_module

    out_dir = Path(out_dir)
    found = ground_module.locate(composition, verbose=verbose)
    instances = instances_from(found)
    if sizes:
        for instance in instances:
            planned = sizes.get(instance.category.lower())
            if planned:
                instance.typical_size_m = planned
    rows = crops(instances, composition, camera, out_dir)
    (out_dir / "instances.json").write_text(json.dumps(rows, indent=1))
    return rows


__all__ = ["Instance", "extract", "crops", "instances_from", "CROP_PX", "CROP_PAD"]
