"""The ground paint chain: jittered guide -> per-region masked conditioning -> blend.

Measured 2026-08-23 (scene-gen lab, 14 places, DreamShaperXL Turbo):
- flat guides need denoise >=0.7 to grow texture and structure dies there; per-pixel
  jitter lets texture emerge at 0.55 where region boundaries stay true to the grid.
  Coarse blotch noise is worse than none — a blotch straddling a boundary reads as
  "terrain crosses here".
- one masked ConditioningSetMask prompt per region stops cross-region word bleed (a
  global prompt painted every region with the same "stone"); mask feather must scale
  with region thickness — a fixed feather dilutes a thin region's conditioning below
  its neighbours' and the region paints as its surroundings.
- a second global img2img at 0.35 deepens texture and unifies lighting; 0.45+ smears
  material identity back to mush.

Everything here is pure: grids and specs in, PIL images and ComfyUI graph dicts out.
The graphs are built in code rather than loaded from config/workflows because the
regional graph's node count follows the map's region count.
"""
from __future__ import annotations

import base64
import io
import uuid
from typing import Dict, List, Tuple

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

CELL = 48
REGIONAL_DENOISE = 0.55
BLEND_DENOISE = 0.35
JITTER = 22
_CHECKPOINT = "DreamShaperXL_Turbo_v2_1.safetensors"

NEGATIVE = ("buildings, houses, trees, people, objects, characters, text, watermark, "
            "blurry, photo, 3d render, grid lines")


def guide_image(grid: List[str], terrain: List[Dict], spec: Dict[str, Dict],
                cell: int = CELL) -> Image.Image:
    """Flat spec-colored regions plus per-pixel jitter — both the placeholder the game
    loads immediately and the init image the regional pass denoises from."""
    colors = {t["symbol"]: spec[t["name"]]["color"] for t in terrain}
    h, w = len(grid), len(grid[0])
    img = Image.new("RGB", (w * cell, h * cell))
    d = ImageDraw.Draw(img)
    for r in range(h):
        for c in range(w):
            d.rectangle([c * cell, r * cell, (c + 1) * cell - 1, (r + 1) * cell - 1],
                        fill=colors.get(grid[r][c], "#9e9e9e"))
    a = np.asarray(img).astype(np.int16)
    rng = np.random.default_rng(7)
    jit = rng.integers(-JITTER, JITTER + 1, a.shape)
    return Image.fromarray(np.clip(a + jit, 0, 255).astype(np.uint8))


def _erosion_depth(a: np.ndarray) -> int:
    b = a.astype(bool)
    d = 0
    while b.any():
        b = (b & np.roll(b, 1, 0) & np.roll(b, -1, 0)
             & np.roll(b, 1, 1) & np.roll(b, -1, 1))
        d += 1
        if d > 32:
            break
    return d


def region_masks(grid: List[str], terrain: List[Dict], cell: int = CELL
                 ) -> List[Tuple[Dict, Image.Image]]:
    """One feathered mask per terrain present in the grid, feather radius from the
    region's erosion depth so thin regions keep hard edges."""
    h, w = len(grid), len(grid[0])
    out = []
    for t in terrain:
        a = np.zeros((h, w), dtype=np.uint8)
        for r in range(h):
            for c in range(w):
                if grid[r][c] == t["symbol"]:
                    a[r][c] = 255
        if not a.any():
            continue
        radius = min(cell // 2, max(0, (_erosion_depth(a) - 1) * cell // 2))
        big = Image.fromarray(np.kron(a, np.ones((cell, cell), dtype=np.uint8)), "L")
        if radius > 1:
            big = big.filter(ImageFilter.GaussianBlur(radius))
        out.append((t, big.convert("RGB")))
    return out


def _b64(img: Image.Image) -> str:
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


def _region_prompt(phrase: str) -> str:
    return (f"top-down painted 2d game terrain, {phrase}, soft painterly texture, "
            "no objects, empty ground")


def global_prompt(spec: Dict[str, Dict]) -> str:
    words = ", ".join(dict.fromkeys(s["phrase"] for s in spec.values()))
    return (f"top-down painted 2d game terrain map, {words}, soft painterly texture, "
            "unified soft lighting, seamless natural transitions, no objects, empty ground")


def regional_payload(grid: List[str], terrain: List[Dict], spec: Dict[str, Dict],
                     seed: int, cell: int = CELL) -> Dict:
    """The first paint job: guide + per-region masked conditioning, one sampler pass."""
    guide = guide_image(grid, terrain, spec, cell)
    init_name = f"scene_guide_{uuid.uuid4().hex}.png"
    uploads = [{"name": init_name, "b64": _b64(guide)}]
    wf = {
        "ck": {"class_type": "CheckpointLoaderSimple",
               "inputs": {"ckpt_name": _CHECKPOINT}},
        "li": {"class_type": "LoadImage", "inputs": {"image": init_name}},
        "n": {"class_type": "CLIPTextEncode", "inputs": {"text": NEGATIVE, "clip": ["ck", 1]}},
        "ve": {"class_type": "VAEEncode", "inputs": {"pixels": ["li", 0], "vae": ["ck", 2]}},
        "vd": {"class_type": "VAEDecode", "inputs": {"samples": ["k", 0], "vae": ["ck", 2]}},
        "s": {"class_type": "SaveImage",
              "inputs": {"images": ["vd", 0], "filename_prefix": "scene_regional"}},
    }
    prev = None
    for i, (t, mask) in enumerate(region_masks(grid, terrain, cell)):
        mask_name = f"scene_mask_{uuid.uuid4().hex}_{t['symbol']}.png"
        uploads.append({"name": mask_name, "b64": _b64(mask)})
        wf[f"lm{i}"] = {"class_type": "LoadImage", "inputs": {"image": mask_name}}
        wf[f"m{i}"] = {"class_type": "ImageToMask",
                       "inputs": {"image": [f"lm{i}", 0], "channel": "red"}}
        wf[f"p{i}"] = {"class_type": "CLIPTextEncode",
                       "inputs": {"text": _region_prompt(spec[t["name"]]["phrase"]),
                                  "clip": ["ck", 1]}}
        wf[f"cm{i}"] = {"class_type": "ConditioningSetMask",
                        "inputs": {"conditioning": [f"p{i}", 0], "mask": [f"m{i}", 0],
                                   "strength": 1.0, "set_cond_area": "default"}}
        if prev is None:
            prev = f"cm{i}"
        else:
            wf[f"cc{i}"] = {"class_type": "ConditioningCombine",
                            "inputs": {"conditioning_1": [prev, 0],
                                       "conditioning_2": [f"cm{i}", 0]}}
            prev = f"cc{i}"
    wf["k"] = {"class_type": "KSampler",
               "inputs": {"seed": seed, "steps": 8, "cfg": 2.5, "sampler_name": "dpmpp_sde",
                          "scheduler": "karras", "denoise": REGIONAL_DENOISE,
                          "model": ["ck", 0], "positive": [prev, 0], "negative": ["n", 0],
                          "latent_image": ["ve", 0]}}
    return {"kind": "comfy_image", "workflow": wf, "uploads": uploads}


def blend_payload(regional_png: bytes, spec: Dict[str, Dict], seed: int) -> Dict:
    """The second paint job: low-denoise global pass over the regional result."""
    init_name = f"scene_blendin_{uuid.uuid4().hex}.png"
    wf = {
        "ck": {"class_type": "CheckpointLoaderSimple",
               "inputs": {"ckpt_name": _CHECKPOINT}},
        "li": {"class_type": "LoadImage", "inputs": {"image": init_name}},
        "p": {"class_type": "CLIPTextEncode",
              "inputs": {"text": global_prompt(spec), "clip": ["ck", 1]}},
        "n": {"class_type": "CLIPTextEncode", "inputs": {"text": NEGATIVE, "clip": ["ck", 1]}},
        "ve": {"class_type": "VAEEncode", "inputs": {"pixels": ["li", 0], "vae": ["ck", 2]}},
        "k": {"class_type": "KSampler",
              "inputs": {"seed": seed, "steps": 8, "cfg": 2.5, "sampler_name": "dpmpp_sde",
                         "scheduler": "karras", "denoise": BLEND_DENOISE, "model": ["ck", 0],
                         "positive": ["p", 0], "negative": ["n", 0],
                         "latent_image": ["ve", 0]}},
        "vd": {"class_type": "VAEDecode", "inputs": {"samples": ["k", 0], "vae": ["ck", 2]}},
        "s": {"class_type": "SaveImage",
              "inputs": {"images": ["vd", 0], "filename_prefix": "scene_blend"}},
    }
    return {"kind": "comfy_image", "workflow": wf,
            "uploads": [{"name": init_name,
                         "b64": base64.b64encode(regional_png).decode("ascii")}]}
