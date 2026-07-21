"""ComfyUI tools for image generation"""

import json
import uuid
import time
import logging
from pathlib import Path
from typing import Dict, Any, List, Optional

import urllib.request
import urllib.parse
import urllib.error

from tools.tool_manager import tool_manager
from tools.safety import screen_image_prompt, log_violation

logger = logging.getLogger(__name__)

_WORKFLOWS_DIR = Path(__file__).parent.parent / "config" / "workflows"
_TXT2IMG_ITEM_WORKFLOW_PATH = _WORKFLOWS_DIR / "txt2img_item.json"
_TXT2IMG_WORKFLOW_PATH = _WORKFLOWS_DIR / "txt2img.json"

# ---------------------------------------------------------------------------
# Prompt construction helpers
# ---------------------------------------------------------------------------


def _build_background_workflow(base_workflow: dict, positive: str, negative: str) -> dict:
    import copy
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


def _get_trellis_settings() -> dict:
    from config.settings_manager import settings_manager
    return settings_manager.get_settings().get("trellis") or {}


def _trellis_endpoint() -> str:
    return _get_trellis_settings().get("endpoint", "http://localhost:8189").rstrip("/")


def run_trellis_batch(sprite_dir: str, out_dir: str) -> set:
    """Run TRELLIS.2 over every sprite in sprite_dir → a textured .glb per slug in out_dir.
    POSTs each sprite to the standalone TRELLIS HTTP server (trellis_server.py — the 4B pipeline
    is already resident there). Returns the set of slugs that produced a .glb. Never raises — a
    failure leaves billboards.

    On the queue transport each sprite is one mesh job instead: the worker holds the retry,
    this side only lands the bytes."""
    import glob
    import os
    from db import queue_client
    if queue_client.enabled():
        return _run_trellis_batch_queued(sprite_dir, out_dir)

    ep = _trellis_endpoint()
    done: set = set()
    for png in sorted(glob.glob(os.path.join(sprite_dir, "*.png"))):
        slug = os.path.splitext(os.path.basename(png))[0]
        with open(png, "rb") as f:
            img = f.read()
        # The pipeline degrades across generates (observed live: 7/14 intermittent 500s with
        # successes in between), so a failure is almost always that, not the image. Retry once —
        # a missing GLB renders as a bare slab.
        for attempt in (1, 2):
            try:
                glb = _http_post_raw(f"{ep}/generate", img, "image/png")
                glb_path = os.path.join(out_dir, f"{slug}.glb")
                with open(glb_path, "wb") as g:
                    g.write(glb)
                _decimate_glb(glb_path)
                done.add(slug)
                break
            except Exception as e:
                logger.error(f"trellis {slug} failed (attempt {attempt}): {e}")
    return done


def _run_trellis_batch_queued(sprite_dir: str, out_dir: str) -> set:
    import base64
    import glob
    import os
    import shutil
    from db import queue_client

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
    import os
    import subprocess
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


def _get_comfyui_settings() -> dict:
    from config.settings_manager import settings_manager
    settings = settings_manager.get_settings()
    return settings.get("comfyui", {})


def _get_comfyui_endpoint() -> str:
    return _get_comfyui_settings().get("endpoint", "http://localhost:8188").rstrip("/")


def _http_post(url: str, data: dict) -> dict:
    payload = json.dumps(data).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        body = resp.read()
        return json.loads(body) if body else {}


def _http_get(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=30) as resp:
        return json.loads(resp.read())


def _http_post_raw(url: str, body: bytes, content_type: str, timeout: int = 1200) -> bytes:
    req = urllib.request.Request(
        url, data=body, headers={"Content-Type": content_type}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def _poll_until_done(endpoint: str, prompt_id: str, timeout: int = 300) -> dict:
    """Poll /history/{prompt_id} until job appears (ComfyUI removes it from queue when done)."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        history = _http_get(f"{endpoint}/history/{prompt_id}")
        if prompt_id in history:
            return history[prompt_id]
        time.sleep(1)
    raise TimeoutError(f"ComfyUI job {prompt_id} did not complete within {timeout}s")


def _save_images_to_working_dir(images: list, endpoint: str) -> list:
    """Download ComfyUI output images into the task working directory. Returns list of saved absolute paths."""
    from tools.execution_context import resolve_base_path
    import shutil

    saved = []
    base_dir = resolve_base_path()
    base_dir.mkdir(parents=True, exist_ok=True)

    for img in images:
        url = img["url"]
        filename = img["filename"]
        dest = base_dir / filename
        try:
            with urllib.request.urlopen(url, timeout=60) as resp:
                with open(dest, "wb") as f:
                    shutil.copyfileobj(resp, f)
            saved.append(str(dest))
            logger.info(f"Saved image: {dest}")
        except Exception as e:
            logger.warning(f"Failed to save image {filename}: {e}")

    return saved


def _save_image_bytes(filename: str, data: bytes) -> str:
    from tools.execution_context import resolve_base_path
    base_dir = resolve_base_path()
    base_dir.mkdir(parents=True, exist_ok=True)
    dest = base_dir / filename
    dest.write_bytes(data)
    logger.info(f"Saved image: {dest}")
    return str(dest)


def _run_comfyui_job_queued(prompt: str, workflow: dict) -> Dict[str, Any]:
    """Hand the resolved workflow to an image worker; it owns the GPU and returns the outputs
    inline, which we land in the working directory exactly like the direct path."""
    from pathlib import Path as _Path

    from db import queue_client

    job = queue_client.run_job("image", {"kind": "comfy_image", "workflow": workflow})
    if job["status"] != "done":
        return {"success": False, "error": job.get("error") or "image job lost"}
    images = (job["result"] or {}).get("images") or []
    # The control plane offloaded each image to <data_dir>/blobs at completion; the row
    # carries paths — same disk as this process.
    saved_paths = [_save_image_bytes(img["filename"], _Path(img["file"]).read_bytes())
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


def _run_comfyui_job(endpoint: str, prompt: str, workflow_override: Optional[dict]) -> Dict[str, Any]:
    """Submit one job to ComfyUI and wait for result."""
    if workflow_override is not None:
        workflow = workflow_override
    else:
        workflow = _load_workflow(_TXT2IMG_WORKFLOW_PATH)
        workflow["11"]["inputs"]["text"] = prompt
        workflow["19"]["inputs"]["seed"] = int(uuid.uuid4().int % (2**32))

    from db import queue_client
    if queue_client.enabled():
        return _run_comfyui_job_queued(prompt, workflow)

    client_id = str(uuid.uuid4())
    queue_resp = _http_post(f"{endpoint}/prompt", {"prompt": workflow, "client_id": client_id})
    prompt_id = queue_resp.get("prompt_id")
    if not prompt_id:
        return {"success": False, "error": f"No prompt_id in response: {queue_resp}"}

    logger.info(f"ComfyUI job queued: {prompt_id}")
    result = _poll_until_done(endpoint, prompt_id)

    outputs = result.get("outputs", {})
    images = []
    for node_output in outputs.values():
        for img in node_output.get("images", []):
            images.append({
                "filename": img["filename"],
                "subfolder": img.get("subfolder", ""),
                "type": img.get("type", "output"),
                "url": (
                    f"{endpoint}/view"
                    f"?filename={urllib.parse.quote(img['filename'])}"
                    f"&subfolder={urllib.parse.quote(img.get('subfolder', ''))}"
                    f"&type={img.get('type', 'output')}"
                ),
            })

    saved_paths = _save_images_to_working_dir(images, endpoint)
    saved_str = ", ".join(saved_paths) if saved_paths else "(none saved)"
    return {
        "success": True,
        "prompt_id": prompt_id,
        "prompt": prompt,
        "images": images,
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
    endpoint = _get_comfyui_endpoint()
    results = []
    for job in jobs:
        violation = screen_image_prompt(job.get("prompt"))
        if violation is not None:
            log_violation(violation, source="image_prompt")
            results.append({"success": False, "error": "blocked by safety filter"})
            continue
        try:
            result = _run_comfyui_job(endpoint, job["prompt"],
                                      job.get("workflow_override"))
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

    endpoint = _get_comfyui_endpoint()
    try:
        return _run_comfyui_job(endpoint, prompt, workflow_override)
    except urllib.error.URLError as e:
        return {"success": False, "error": f"Cannot reach ComfyUI at {endpoint}: {e.reason}. Is ComfyUI running with --listen?"}
    except TimeoutError as e:
        return {"success": False, "error": str(e)}
    except Exception as e:
        logger.error(f"generate_image failed: {e}")
        return {"success": False, "error": str(e)}
