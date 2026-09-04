"""The frames of an `anim` — what the video worker does to MiniMax's output between the clips and
the one sheet the game loads. Pure functions over PIL images and numpy arrays: nothing here talks
to ComfyUI, so every rule is testable on synthetic frames.

A thing seen from the side is one TURNTABLE clip — pinned to the still at both ends, one turn
in place — that yields the three other facing stills; a thing with one view skips it. Then one
PINNED clip per (direction, animation) whose first and last frames are that still, so the loop
closes by construction. The sheet is rows of (animation, direction) and
columns of frames, every cell the same size, every frame cropped to ONE box shared across the
character so the feet never jump between cells.
"""

from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
from PIL import Image, ImageDraw

STILL_SIZE = 768
DIRS = ("front", "right", "back", "left")
MATTE_TOLERANCE = 30
# A frame whose whole-image change from its predecessor is under this is the pin holding still.
STILL_DIFF = 2.0
# Change from the first frame (mean abs diff on 0–255) a clip must reach somewhere, or the
# model held still and the loop is a sway. Real walks, swings and idles measured 25–40.
WEAK_MOTION = 15.0
_KEY = (255, 0, 255)
# Appended to a loop clip's prompt so the model keeps the facing the still shows.
FACING_PHRASES = {"front": "seen from the front, facing the camera",
                  "right": "seen from the side, facing right",
                  "back": "seen from behind, facing away from the camera",
                  "left": "seen from the side, facing left"}


def prep_still(im: Image.Image) -> Image.Image:
    """A matted sprite becomes the clip's first frame: on white, square, STILL_SIZE — the size the
    model was trained near, and white because the matte comes back off it by flood fill."""
    rgba = im.convert("RGBA")
    side = max(rgba.size)
    canvas = Image.new("RGBA", (side, side), (255, 255, 255, 255))
    canvas.alpha_composite(rgba, ((side - rgba.width) // 2, (side - rgba.height) // 2))
    return canvas.convert("RGB").resize((STILL_SIZE, STILL_SIZE), Image.LANCZOS)


def _gray(frames: Sequence[Image.Image]) -> np.ndarray:
    return np.stack([np.asarray(f.convert("L"), dtype=np.float32) for f in frames])


def motion_signal(frames: Sequence[Image.Image]) -> np.ndarray:
    """Mean absolute change from the previous frame, whole image; element 0 is 0."""
    g = _gray(frames)
    out = np.zeros(len(frames), dtype=np.float32)
    out[1:] = np.abs(g[1:] - g[:-1]).mean(axis=(1, 2))
    return out


def peak_motion(frames: Sequence[Image.Image]) -> float:
    """The most any frame differs from the first."""
    g = _gray(frames)
    return float(np.abs(g[1:] - g[0]).mean(axis=(1, 2)).max()) if len(g) > 1 else 0.0


def pick_facings(frames: Sequence[Image.Image]) -> Dict[str, int]:
    """Which turntable frame shows each facing. The clip is pinned to the front still at both
    ends and turns once through screen-right, away, screen-left at a steady rate, so the
    facings sit at the quarter marks of the span that moves. The span is what the model
    decides, not the clip length: the pins hold still for a frame or two at each end. Measured
    on three characters (knight, troll, specter): the quarter marks landed on the true
    profiles and back every time, while scoring frames by silhouette symmetry missed by two
    to three frames whenever a held weapon made the back lopsided. The first frame is the
    front, and is not chosen: the caller already holds the still it was made from."""
    n = len(frames)
    moving = np.flatnonzero(motion_signal(frames) > STILL_DIFF)
    start, end = (int(moving[0]) - 1, int(moving[-1])) if len(moving) else (0, n - 1)
    span = end - start
    return {"front": 0,
            "right": min(n - 1, start + int(round(span * 0.25))),
            "back": min(n - 1, start + int(round(span * 0.5))),
            "left": min(n - 1, start + int(round(span * 0.75)))}


def matte(frame: Image.Image) -> Image.Image:
    """Alpha from the white background: a flood fill from every corner, so an enclosed white
    region — between an arm and the body, inside a shield's boss — stays part of the subject."""
    rgb = frame.convert("RGB").copy()
    w, h = rgb.size
    for corner in ((0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1)):
        ImageDraw.floodfill(rgb, corner, _KEY, thresh=MATTE_TOLERANCE)
    arr = np.asarray(rgb)
    keyed = np.all(arr == _KEY, axis=2)
    out = frame.convert("RGBA")
    alpha = np.where(keyed, 0, 255).astype(np.uint8)
    out.putalpha(Image.fromarray(alpha, "L"))
    return out


def trim_pinned(frames: Sequence[Image.Image]) -> List[Image.Image]:
    """A pinned clip holds the still for a few frames at each end; the loop is what lies
    between — drop the leading and trailing frames that do not move."""
    motion = motion_signal(frames)
    moving = np.flatnonzero(motion > STILL_DIFF)
    if len(moving) == 0:
        return list(frames)
    return list(frames[max(0, int(moving[0]) - 1):int(moving[-1]) + 1])


def sample(frames: Sequence[Image.Image], count: int) -> List[Image.Image]:
    if len(frames) <= count:
        return list(frames)
    return [frames[int(i * (len(frames) - 1) / (count - 1))] for i in range(count)]


def shared_box(frames: Sequence[Image.Image], pad_frac: float = 0.04) -> Tuple[int, int, int, int]:
    """The union of every frame's opaque bbox, padded — one crop for the whole character."""
    boxes = [f.split()[-1].getbbox() for f in frames]
    boxes = [b for b in boxes if b]
    if not boxes:
        return (0, 0, frames[0].width, frames[0].height)
    w, h = frames[0].size
    l = min(b[0] for b in boxes)
    t = min(b[1] for b in boxes)
    r = max(b[2] for b in boxes)
    b_ = max(b[3] for b in boxes)
    pad = int(max(r - l, b_ - t) * pad_frac)
    return (max(0, l - pad), max(0, t - pad), min(w, r + pad), min(h, b_ + pad))


def pack(clips: Dict[Tuple[str, str], List[Image.Image]], anims: Dict[str, Dict],
         dirs: Sequence[str] = DIRS, cell_height: int = 128,
         warnings: Optional[List[str]] = None) -> Tuple[Image.Image, Dict]:
    """The sheet and its manifest. `clips` maps (anim, dir) to matted, sampled frames; `anims`
    maps a name to {"fps"}; `dirs` is the facings this thing has, in row order. Rows are anims
    in `anims` order, dirs in `dirs` order within each; a (anim, dir) with no clip has no row,
    and the manifest's `rows` omits it so the player can mirror or fall back. Cell size comes
    from the shared box scaled to `cell_height`; the pivot is the feet's centre — bottom middle
    of the box less its padding."""
    every = [f for frames in clips.values() for f in frames]
    box = shared_box(every)
    bw, bh = box[2] - box[0], box[3] - box[1]
    scale = cell_height / bh
    cw = max(1, int(round(bw * scale)))
    rows: List[Tuple[str, str, List[Image.Image]]] = []
    for anim in anims:
        for d in dirs:
            frames = clips.get((anim, d))
            if frames:
                rows.append((anim, d, frames))
    cols = max(len(r[2]) for r in rows)
    sheet = Image.new("RGBA", (cw * cols, cell_height * len(rows)), (0, 0, 0, 0))
    manifest_anims: Dict[str, Dict] = {a: {"rows": {}, "frames": 0, "fps": spec["fps"]}
                                       for a, spec in anims.items()}
    for row, (anim, d, frames) in enumerate(rows):
        for col, f in enumerate(frames):
            cell = f.crop(box).resize((cw, cell_height), Image.LANCZOS)
            sheet.alpha_composite(cell, (col * cw, row * cell_height))
        manifest_anims[anim]["rows"][d] = row
        manifest_anims[anim]["frames"] = max(manifest_anims[anim]["frames"], len(frames))
    pad = int(max(bw, bh) * 0.04)
    manifest = {"cell": {"w": cw, "h": cell_height},
                "dirs": list(dirs),
                "anims": {a: m for a, m in manifest_anims.items() if m["rows"]},
                "pivot": {"x": cw / 2, "y": max(0.0, (bh - pad) * scale)},
                "warnings": list(warnings or [])}
    return sheet, manifest
