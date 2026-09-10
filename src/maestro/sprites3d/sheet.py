"""Four camera angles x N frames of a rigged, animated glb, packed as a sprite sheet.

The facings a drawn sheet has to derive from a turntable — and gets wrong — are camera azimuths
here, so they are exact and consistent by construction. Frame count, fps and loop points are the
game's to choose rather than a video model's.

Two things are not obvious:

  * a generated clip is mostly DEAD. An 8.3s clip put its attack in 0.27-1.87s, so sampling the
    clip's length spends most cells on a held pose; the sheet samples the window the motion is
    actually in.
  * every cell is cropped against ONE box, the union over every frame and facing, or the character
    jumps around inside its own sprite.
"""
import base64
import io
import json
import math
import struct
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
from PIL import Image

VIEWER = Path(__file__).with_name("viewer.html")
DIRS = {"front": 0.0, "right": math.pi / 2, "back": math.pi, "left": 3 * math.pi / 2}


def _accessor(glb: bytes, j: Dict, index: int) -> np.ndarray:
    a = j["accessors"][index]
    v = j["bufferViews"][a["bufferView"]]
    header = 20 + struct.unpack("<I", glb[12:16])[0] + 8
    off = header + v.get("byteOffset", 0) + a.get("byteOffset", 0)
    width = {"VEC4": 4, "VEC3": 3, "SCALAR": 1}[a["type"]]
    return np.frombuffer(glb[off:off + a["count"] * width * 4],
                         dtype=np.float32).reshape(a["count"], width)


def window_of(times: np.ndarray, speed: np.ndarray, keep: float = 0.92) -> Tuple[float, float]:
    """The span carrying `keep` of the total rotation, given per-step rotation speed."""
    if speed is None or times is None or not np.any(speed):
        return 0.0, float(times[-1]) if times is not None and len(times) else 1.0
    cum = np.cumsum(speed) / np.sum(speed)
    lo = int(np.searchsorted(cum, (1 - keep) / 2))
    hi = int(np.searchsorted(cum, 1 - (1 - keep) / 2))
    return float(times[max(0, lo)]), float(times[min(len(times) - 1, hi + 1)])


def active_window(glb_path: str, keep: float = 0.92) -> Tuple[float, float]:
    """The span of a clip that carries `keep` of its rotation, so the dead head and tail of a
    generated clip do not eat the sheet's cells."""
    glb = Path(glb_path).read_bytes()
    j = json.loads(glb[20:20 + struct.unpack("<I", glb[12:16])[0]])
    anim = j["animations"][0]
    speed, times = None, None
    for ch in anim["channels"]:
        if ch["target"]["path"] != "rotation":
            continue
        sampler = anim["samplers"][ch["sampler"]]
        times = _accessor(glb, j, sampler["input"])[:, 0]
        q = _accessor(glb, j, sampler["output"])
        q = q / np.linalg.norm(q, axis=1, keepdims=True)
        step = np.degrees(2 * np.arccos(np.clip(np.abs(np.sum(q[1:] * q[:-1], axis=1)), -1, 1)))
        speed = step if speed is None else speed + step
    return window_of(times, speed, keep)


def pack(clips: Dict[Tuple[str, str], List[Image.Image]], anims: Sequence[str],
         cell_height: int = 128, fps: int = 12,
         loops: Optional[Dict[str, bool]] = None) -> Tuple[Image.Image, Dict]:
    """The sheet and the manifest `lib/sprites.js` reads."""
    boxes = [im.split()[-1].getbbox() for ims in clips.values() for im in ims
             if im.split()[-1].getbbox()]
    if not boxes:
        raise ValueError("every rendered frame is empty")
    x0 = min(b[0] for b in boxes)
    y0 = min(b[1] for b in boxes)
    x1 = max(b[2] for b in boxes)
    y1 = max(b[3] for b in boxes)
    cell_w = max(1, int(round((x1 - x0) * cell_height / (y1 - y0))))
    rows = [(a, d) for a in anims for d in DIRS]
    frames = len(next(iter(clips.values())))
    sheet = Image.new("RGBA", (cell_w * frames, cell_height * len(rows)), (0, 0, 0, 0))
    manifest = {"cell": {"w": cell_w, "h": cell_height}, "dirs": list(DIRS), "anims": {},
                "pivot": {"x": cell_w / 2.0, "y": cell_height - 4.0}}
    for r, (anim, d) in enumerate(rows):
        manifest["anims"].setdefault(anim, {"rows": {}, "frames": frames, "fps": fps,
                                            "loops": bool((loops or {}).get(anim, True))})
        manifest["anims"][anim]["rows"][d] = r
        for i, im in enumerate(clips[(anim, d)]):
            sheet.paste(im.crop((x0, y0, x1, y1)).resize((cell_w, cell_height), Image.LANCZOS),
                        (i * cell_w, r * cell_height))
    return sheet, manifest


async def render(page, glb_url: str, frames: int, start: float, end: float) -> Dict[str, List[Image.Image]]:
    """One animated glb as {facing: [frame, ...]}.

    The window is passed in rather than read off the page: navigating clears anything set on it,
    and a lost window silently samples NaN — every cell then renders the bind pose and the sheet
    comes back as one frame repeated sixty-four times."""
    await page.goto(glb_url)
    await page.wait_for_function("window.__ready===true", timeout=120000)
    span = max(1, frames - 1)
    out = {}
    for d, azimuth in DIRS.items():
        ims = []
        for i in range(frames):
            at = start + (end - start) * i / span
            await page.evaluate(f"window.__at({at}); window.__view({azimuth})")
            data = await page.evaluate("document.querySelector('canvas').toDataURL('image/png')")
            ims.append(Image.open(io.BytesIO(base64.b64decode(data.split(",")[1]))).convert("RGBA"))
        out[d] = ims
    return out
