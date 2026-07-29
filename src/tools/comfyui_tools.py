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


def _build_background_workflow(base_workflow: dict, positive: str, negative: str) -> dict:
    wf = copy.deepcopy(base_workflow)
    wf["6"]["inputs"]["text"] = positive
    wf["7"]["inputs"]["text"] = negative
    wf["3"]["inputs"]["seed"] = int(uuid.uuid4().int % (2**32))
    return wf


_ITEM_NEGATIVE = (
    "worst quality, low quality, blurry, distorted, scenery, landscape, environment, room, "
    "wide angle, people, person, 1girl, 1boy, hands, multiple objects, cluttered, cropped, "
    "watermark, signature, text"
)


def build_item_job(description: str) -> dict:
    """Return a {prompt, workflow_override} job dict for ONE inventory item icon (square — icons
    render small in the inventory bar, so the object must fill the frame, never sit in a scene).
    `description` is the manifest's SAVED styled prompt and IS the positive, verbatim: the flux
    workflow's T5 encoder reads prose as-is, danbooru quality tags are off-distribution here, and
    embedding the prose mid-phrase ("a single {X}, one object only, ...") or salting it with tags
    drove subject drift."""
    positive = description
    # txt2img_item = the background workflow + BiRefNet matting: these render ON maps and in
    # the inventory bar, so they must land transparent like sprites (observed: props shipping
    # with baked backgrounds).
    wf = _build_background_workflow(
        _load_workflow(_TXT2IMG_ITEM_WORKFLOW_PATH), positive, _ITEM_NEGATIVE)
    wf["5"]["inputs"]["width"] = 1024
    wf["5"]["inputs"]["height"] = 1024
    return {"prompt": positive, "workflow_override": wf}


def build_img2img_item_job(description: str, init_name: str, denoise: float = 0.6) -> dict:
    """The item workflow seeded from an EXISTING render instead of an empty latent: the init image
    (uploaded to ComfyUI under `init_name` by the worker) is VAE-encoded and partially denoised, so
    the output keeps the original's composition while the prompt steers the change. Same prompt
    conventions and BiRefNet matting as build_item_job."""
    wf = _build_background_workflow(
        _load_workflow(_IMG2IMG_ITEM_WORKFLOW_PATH), description, _ITEM_NEGATIVE)
    wf["50"]["inputs"]["image"] = init_name
    wf["3"]["inputs"]["denoise"] = denoise
    return {"prompt": description, "workflow_override": wf}


def build_item_payload(description: str, init_image_b64: Optional[str] = None,
                       denoise: float = 0.6) -> Optional[Dict[str, Any]]:
    """The queue payload for ONE item icon, safety-screened. None = blocked, never sent.

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
        return {"kind": "comfy_image", "workflow": build_item_job(description)["workflow_override"]}
    init_name = f"init_{uuid.uuid4().hex}.png"
    wf = build_img2img_item_job(description, init_name, denoise)["workflow_override"]
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


