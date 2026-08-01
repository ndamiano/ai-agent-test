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


# INERT on the current sampler and kept only because the node must carry something: flux schnell
# runs at cfg 1.0, and ComfyUI skips the uncond pass entirely at cfg 1, so nothing here reaches the
# model. It is not what separates the kinds — the matte is. (The old negative banned "person" and
# "room"; the renders that proved this came back a person and a room.)
_NEGATIVE = "worst quality, low quality, blurry, distorted, watermark, signature, text"

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


def build_image_job(description: str, kind: str = "sprite") -> dict:
    """Return a {prompt, workflow_override} job dict for ONE rendered image.

    `description` is the manifest's SAVED prompt and IS the positive, verbatim: the flux workflow's
    T5 encoder reads prose as-is, danbooru quality tags are off-distribution here, and embedding the
    prose mid-phrase ("a single {X}, one object only, ...") or salting it with tags drove subject
    drift. What `kind` changes is whether the matte runs — never the prose, and never the negative,
    which this sampler does not read at all."""
    wf = _build_background_workflow(
        _load_workflow(_TXT2IMG_ITEM_WORKFLOW_PATH), description, _NEGATIVE)
    wf["5"]["inputs"]["width"] = 1024
    wf["5"]["inputs"]["height"] = 1024
    if kind not in MATTED_KINDS:
        _drop_matte(wf)
    return {"prompt": description, "workflow_override": wf}


def build_img2img_job(description: str, init_name: str, kind: str = "sprite",
                      denoise: float = 0.6) -> dict:
    """The same workflow seeded from an EXISTING render instead of an empty latent: the init image
    (uploaded to ComfyUI under `init_name` by the worker) is VAE-encoded and partially denoised, so
    the output keeps the original's composition while the prompt steers the change."""
    wf = _build_background_workflow(
        _load_workflow(_IMG2IMG_ITEM_WORKFLOW_PATH), description, _NEGATIVE)
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


