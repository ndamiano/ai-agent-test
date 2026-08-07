"""Image + mesh job PAYLOADS: this side resolves a workflow and screens the prompt, the worker next
to the GPU runs it, and `_decimate_glb` is the one local post-op the asset chain calls on a result.

Nothing here enqueues or waits — a caller lands the payload on the `image` queue itself, so every
producer of GPU work passes through one place that can meter it.
"""

import copy
import json
import logging
import os
import subprocess
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

from tools.safety import log_violation, screen_image_prompt

logger = logging.getLogger(__name__)

_WORKFLOWS_DIR = Path(__file__).parent.parent / "config" / "workflows"
_TXT2IMG_ITEM_WORKFLOW_PATH = _WORKFLOWS_DIR / "txt2img_item.json"
_IMG2IMG_ITEM_WORKFLOW_PATH = _WORKFLOWS_DIR / "img2img_item.json"
_TXT2IMG_TILE_WORKFLOW_PATH = _WORKFLOWS_DIR / "txt2img_tile.json"
_IMG2IMG_TILE_WORKFLOW_PATH = _WORKFLOWS_DIR / "img2img_tile.json"


def _build_background_workflow(base_workflow: dict, positive: str, negative: str) -> dict:
    wf = copy.deepcopy(base_workflow)
    wf["6"]["inputs"]["text"] = positive
    wf["7"]["inputs"]["text"] = negative
    wf["3"]["inputs"]["seed"] = int(uuid.uuid4().int % (2**32))
    return wf


# Both samplers run at a real cfg now, so the negative reaches the model. The item negative pushes
# away from photoreal (an anime checkpoint asked for game art); tiles measured today drift photoreal
# and grow cracks and objects without their extra terms.
_NEGATIVE_ITEM = "worst quality, low quality, blurry, watermark, signature, text, photo, " \
                 "photorealistic"
_NEGATIVE_TILE = _NEGATIVE_ITEM + ", people, person, animal, border, frame, vignette, cracks, " \
                 "cracked ground"

# NetaYume is danbooru-trained, so quality tags are on-distribution and prepend cleanly. Tiles
# render through DreamShaperXL, where they are not, so tile positives stay verbatim.
_POSITIVE_PREFIX_ITEM = "masterpiece, best quality, "

# A sprite is composited onto the game's own background, so it is matted to its subject. A tile or
# a backdrop IS the background: matting one leaves the ragged fragments of a floor that used to be
# a floor, so those keep the full opaque frame the sampler drew.
MATTED_KINDS = ("sprite",)

_MATTE_NODE = "47"
_DECODE_NODE = "8"
_OUTPUT_NODE = "9"


def _drop_matte(wf: dict) -> dict:
    """Take BiRefNet out of the graph: the output reads the VAE decode directly."""
    wf[_OUTPUT_NODE]["inputs"]["images"] = [_DECODE_NODE, 0]
    wf.pop(_MATTE_NODE, None)
    return wf


def _kind_recipe(kind: str, txt2img: bool) -> tuple:
    """(workflow path, positive prefix, negative) for a kind: tiles render through DreamShaperXL
    Turbo, everything else through NetaYume Lumina."""
    if kind == "tile":
        path = _TXT2IMG_TILE_WORKFLOW_PATH if txt2img else _IMG2IMG_TILE_WORKFLOW_PATH
        return path, "", _NEGATIVE_TILE
    path = _TXT2IMG_ITEM_WORKFLOW_PATH if txt2img else _IMG2IMG_ITEM_WORKFLOW_PATH
    return path, _POSITIVE_PREFIX_ITEM, _NEGATIVE_ITEM


def build_image_job(description: str, kind: str = "sprite") -> dict:
    """Return a {prompt, workflow_override} job dict for ONE rendered image.

    `description` is the manifest's SAVED prompt and the whole of the positive's substance: quality
    tags are prepended for the anime checkpoint, but the prose is never embedded mid-phrase ("a
    single {X}, one object only, ...") — that garbled the grammar and drove subject drift. `kind`
    picks the model, the negative and whether the matte runs."""
    path, prefix, negative = _kind_recipe(kind, txt2img=True)
    wf = _build_background_workflow(_load_workflow(path), prefix + description, negative)
    wf["5"]["inputs"]["width"] = 1024
    wf["5"]["inputs"]["height"] = 1024
    if kind not in MATTED_KINDS:
        _drop_matte(wf)
    return {"prompt": description, "workflow_override": wf}


def build_img2img_job(description: str, init_name: str, kind: str = "sprite",
                      denoise: float = 0.6) -> dict:
    """The same per-kind workflow seeded from an EXISTING render instead of an empty latent: the
    init image (uploaded to ComfyUI under `init_name` by the worker) is VAE-encoded and partially
    denoised, so the output keeps the original's composition while the prompt steers the change."""
    path, prefix, negative = _kind_recipe(kind, txt2img=False)
    wf = _build_background_workflow(_load_workflow(path), prefix + description, negative)
    wf["50"]["inputs"]["image"] = init_name
    wf["3"]["inputs"]["denoise"] = denoise
    if kind not in MATTED_KINDS:
        _drop_matte(wf)
    return {"prompt": description, "workflow_override": wf}


def build_image_payload(description: str, kind: str = "sprite",
                        init_image_b64: Optional[str] = None,
                        denoise: float = 0.6) -> Optional[Dict[str, Any]]:
    """The queue payload for ONE image, safety-screened. None = blocked, never sent.

    With `init_image_b64` the job is img2img: the payload carries the init image as an upload the
    worker lands on ComfyUI before submitting, and the workflow denoises from it instead of noise.

    The local image model has no built-in guardrails, so every finalized prompt is screened before
    it can reach a worker. A blocked prompt degrades like any other missing asset — the game renders
    that entity as its shape."""
    violation = screen_image_prompt(description)
    if violation is not None:
        log_violation(violation, source="image_prompt")
        return None
    if init_image_b64 is None:
        return {"kind": "comfy_image",
                "workflow": build_image_job(description, kind)["workflow_override"]}
    init_name = f"init_{uuid.uuid4().hex}.png"
    wf = build_img2img_job(description, init_name, kind, denoise)["workflow_override"]
    return {"kind": "comfy_image", "workflow": wf,
            "uploads": [{"name": init_name, "b64": init_image_b64}]}


def _decimate_glb(glb_path: str) -> bool:
    """Shrink a raw TRELLIS GLB to game weight (~16MB → ~1MB; runtime/decimate.mjs). Soft — a
    failure keeps the fat original (heavy but playable), never a broken file."""
    runtime = Path(__file__).resolve().parents[2] / "runtime"
    tmp = f"{glb_path}.dec.glb"
    try:
        p = subprocess.run(["node", str(runtime / "decimate.mjs"), glb_path, tmp],
                           capture_output=True, text=True, timeout=300, cwd=runtime)
        if p.returncode == 0 and os.path.exists(tmp) and os.path.getsize(tmp) > 0:
            os.replace(tmp, glb_path)
            return True
        logger.warning(f"decimate kept original for {os.path.basename(glb_path)}: "
                       f"{(p.stderr or p.stdout)[-200:]}")
    except Exception as e:
        logger.warning(f"decimate kept original for {os.path.basename(glb_path)}: {e}")
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    return False


def _load_workflow(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


