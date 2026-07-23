"""Image + mesh generation. Both are worker-pull queue jobs: this side builds the workflow and
lands the returned bytes, the worker next to the GPU runs ComfyUI and TRELLIS. There is no
direct-call path — every producer of GPU work goes through db.queue_client so it can be metered
and budget-gated in one place.
"""

import base64
import copy
import glob
import json
import logging
import os
import shutil
import subprocess
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from db import queue_client
from tools.execution_context import resolve_base_path
from tools.safety import log_violation, screen_image_prompt
from tools.tool_manager import tool_manager

logger = logging.getLogger(__name__)

_WORKFLOWS_DIR = Path(__file__).parent.parent / "config" / "workflows"
_TXT2IMG_ITEM_WORKFLOW_PATH = _WORKFLOWS_DIR / "txt2img_item.json"
_TXT2IMG_WORKFLOW_PATH = _WORKFLOWS_DIR / "txt2img.json"

# ---------------------------------------------------------------------------
# Prompt construction helpers
# ---------------------------------------------------------------------------


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
    """Return a {prompt, workflow_override} job dict for ONE inventory item icon (WAI
    Illustrious, square — icons render small in the inventory bar, so the object must fill the
    frame, never sit in a scene). `description` is the manifest's SAVED styled prompt and IS the
    positive, verbatim: the flux workflow's T5 encoder reads prose as-is — danbooru quality tags
    and negative prompts are tag-model (Illustrious) culture and off-distribution here. The
    tag-model era taught the hard lesson: embedding the prose mid-phrase ("a single {X}, one
    object only, ...") or salting it with tags drove subject drift."""
    positive = description
    # txt2img_item = the background workflow + BiRefNet matting: these render ON maps and in
    # the inventory bar, so they must land transparent like sprites (observed: props shipping
    # with baked backgrounds).
    wf = _build_background_workflow(
        _load_workflow(_TXT2IMG_ITEM_WORKFLOW_PATH), positive, _ITEM_NEGATIVE)
    wf["5"]["inputs"]["width"] = 1024
    wf["5"]["inputs"]["height"] = 1024
    return {"prompt": positive, "workflow_override": wf}


def run_trellis_batch(sprite_dir: str, out_dir: str) -> set:
    """Run TRELLIS.2 over every sprite in sprite_dir → a textured .glb per slug in out_dir. Each
    sprite is one `mesh` job: the worker owns the GPU and the retry, this side only lands the
    bytes. Returns the set of slugs that produced a .glb. Never raises — a failure leaves
    billboards."""
    done: set = set()
    for png in sorted(glob.glob(os.path.join(sprite_dir, "*.png"))):
        slug = os.path.splitext(os.path.basename(png))[0]
        with open(png, "rb") as f:
            img_b64 = base64.b64encode(f.read()).decode("ascii")
        # Cold start on an autoscaled pod (boot + image pull + pipeline lazy-load) plus the
        # generate itself can exceed the 900s queue default.
        job = queue_client.run_job("mesh", {"kind": "trellis_mesh", "image_b64": img_b64},
                                   timeout_seconds=1800)
        if job["status"] != "done":
            logger.error(f"trellis {slug} failed: {job.get('error')}")
            continue
        glb_path = os.path.join(out_dir, f"{slug}.glb")
        # The control plane offloaded the GLB to <data_dir>/blobs at completion (workqueue
        # router); the jobs row carries only the path — same disk as this process.
        shutil.copyfile(job["result"]["glb_file"], glb_path)
        _decimate_glb(glb_path)
        done.add(slug)
    return done


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


def _save_image_bytes(filename: str, data: bytes) -> str:
    base_dir = resolve_base_path()
    base_dir.mkdir(parents=True, exist_ok=True)
    dest = base_dir / filename
    dest.write_bytes(data)
    logger.info(f"Saved image: {dest}")
    return str(dest)


def _run_comfyui_job(prompt: str, workflow_override: Optional[dict]) -> Dict[str, Any]:
    """Resolve the workflow and hand it to an image worker; the worker owns the GPU and the
    submit/poll/fetch flow, this side only lands the bytes in the working directory."""
    if workflow_override is not None:
        workflow = workflow_override
    else:
        workflow = _load_workflow(_TXT2IMG_WORKFLOW_PATH)
        workflow["11"]["inputs"]["text"] = prompt
        workflow["19"]["inputs"]["seed"] = int(uuid.uuid4().int % (2**32))

    job = queue_client.run_job("image", {"kind": "comfy_image", "workflow": workflow})
    if job["status"] != "done":
        return {"success": False, "error": job.get("error") or "image job lost"}
    images = (job["result"] or {}).get("images") or []
    # The control plane offloaded each image to <data_dir>/blobs at completion; the row
    # carries paths — same disk as this process.
    saved_paths = [_save_image_bytes(img["filename"], Path(img["file"]).read_bytes())
                   for img in images]
    saved_str = ", ".join(saved_paths) if saved_paths else "(none saved)"
    return {
        "success": True,
        "prompt_id": (job["result"] or {}).get("prompt_id"),
        "prompt": prompt,
        "images": [{"filename": img["filename"]} for img in images],
        "image_count": len(images),
        "saved_paths": saved_paths,
        "message": f"Image generated successfully. Saved to: {saved_str}",
    }


def run_jobs(jobs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Run image jobs sequentially. Each job: {"prompt": str, "workflow_override": dict | None}.

    The local image model (uncensored SDXL) has no built-in guardrails, so every finalized
    prompt is screened here before it reaches the model — the one chokepoint every image job
    funnels through. A flagged prompt is skipped (never sent) and degrades like any other failed
    job (placeholder/fallback in the caller); it never crashes the build."""
    results = []
    for job in jobs:
        violation = screen_image_prompt(job.get("prompt"))
        if violation is not None:
            log_violation(violation, source="image_prompt")
            results.append({"success": False, "error": "blocked by safety filter"})
            continue
        try:
            result = _run_comfyui_job(job["prompt"], job.get("workflow_override"))
        except Exception as e:
            logger.error(f"run_jobs job failed: {e}")
            result = {"success": False, "error": str(e)}
        results.append(result)
    return results


@tool_manager.tool(
    description="Generate an image from a text prompt using ComfyUI. Returns URLs and filenames for the generated image(s). Use this when asked to create, draw, or visualize anything.",
    auto_inject_context=False,
)
def generate_image(prompt: str, workflow_override: Optional[dict] = None) -> Dict[str, Any]:
    violation = screen_image_prompt(prompt)
    if violation is not None:
        log_violation(violation, source="generate_image_tool")
        return {"success": False, "error": "blocked by safety filter"}

    try:
        return _run_comfyui_job(prompt, workflow_override)
    except Exception as e:
        logger.error(f"generate_image failed: {e}")
        return {"success": False, "error": str(e)}
