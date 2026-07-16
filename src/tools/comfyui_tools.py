"""ComfyUI tools for image generation"""

import json
import uuid
import time
import logging
from contextlib import contextmanager
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

    The 4B pipeline needs ~19GB and the mesh pass runs after the LLM/ComfyUI phase (they may be
    resident), so free everyone's VRAM first, then unload trellis after so they can reclaim it."""
    import glob
    import os
    ep = _trellis_endpoint()
    _comfyui_free_vram(_get_comfyui_endpoint())
    loaded = _llm_get_loaded_model()
    if loaded:
        _llm_unload(loaded)

    def _unload() -> None:
        try:
            _http_post_raw(f"{ep}/unload", b"", "application/json", timeout=60)
        except Exception:
            pass

    done: set = set()
    for i, png in enumerate(sorted(glob.glob(os.path.join(sprite_dir, "*.png")))):
        # The resident pipeline leaks VRAM across generates (observed live: 70s/mesh at batch
        # start, 160s+ and CuMesh OOM 500s by mesh ~30 on a 32GB card). A periodic unload
        # (lazy reload ~45s) resets it — far cheaper than the degradation.
        if i and i % 10 == 0:
            _unload()
        slug = os.path.splitext(os.path.basename(png))[0]
        try:
            with open(png, "rb") as f:
                glb = _http_post_raw(f"{ep}/generate", f.read(), "image/png")
            glb_path = os.path.join(out_dir, f"{slug}.glb")
            with open(glb_path, "wb") as g:
                g.write(glb)
            done.add(slug)
        except Exception as e:
            logger.error(f"trellis {slug} failed: {e}")
    _unload()
    return done


def _load_workflow(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _get_comfyui_settings() -> dict:
    from config.settings_manager import settings_manager
    settings = settings_manager.get_settings()
    return settings.get("comfyui", {})


def _get_comfyui_endpoint() -> str:
    return _get_comfyui_settings().get("endpoint", "http://localhost:8188").rstrip("/")


def _get_lmstudio_base_url() -> str:
    from config.settings_manager import settings_manager
    settings = settings_manager.get_settings()
    return settings.get("lmstudio", {}).get("base_url", "http://localhost:1234").rstrip("/")


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


def _lmstudio_get_loaded_model() -> Optional[str]:
    """Return identifier of first loaded model in LM Studio, or None."""
    try:
        base_url = _get_lmstudio_base_url()
        data = _http_get(f"{base_url}/api/v0/models")
        models = data.get("data", [])
        for m in models:
            if m.get("state") == "loaded":
                return m.get("id")
        return None
    except Exception as e:
        logger.warning(f"Could not query LM Studio models: {e}")
        return None


def _lmstudio_unload(model_id: str, wait_timeout: int = 30) -> bool:
    """Unload a model from LM Studio and wait until it is no longer loaded."""
    try:
        base_url = _get_lmstudio_base_url()
        _http_post(f"{base_url}/api/v1/models/unload", {"instance_id": model_id})
        logger.info(f"LM Studio: unload requested for {model_id}, waiting for VRAM release...")
    except Exception as e:
        logger.warning(f"LM Studio unload failed: {e}")
        return False

    # Poll until model is no longer in loaded state
    deadline = time.time() + wait_timeout
    while time.time() < deadline:
        try:
            base_url = _get_lmstudio_base_url()
            data = _http_get(f"{base_url}/api/v0/models")
            loaded_ids = {m.get("id") for m in data.get("data", []) if m.get("state") == "loaded"}
            if model_id not in loaded_ids:
                logger.info(f"LM Studio: {model_id} confirmed unloaded")
                return True
        except Exception:
            pass
        time.sleep(1)

    logger.warning(f"LM Studio: {model_id} did not confirm unload within {wait_timeout}s — proceeding anyway")
    return False


def _lmstudio_load(model_id: str) -> bool:
    """Load a model into LM Studio. Returns True on success."""
    try:
        base_url = _get_lmstudio_base_url()
        _http_post(f"{base_url}/api/v1/models/load", {"model": model_id})
        logger.info(f"LM Studio: loaded {model_id}")
        return True
    except Exception as e:
        logger.warning(f"LM Studio load failed: {e}")
        return False


def _llamacpp_get_loaded_model() -> Optional[str]:
    """Return the id of the first model the llama.cpp router reports as loaded, or None."""
    try:
        base_url = _get_lmstudio_base_url()
        data = _http_get(f"{base_url}/models")
        for m in data.get("data", []):
            if m.get("status", {}).get("value") == "loaded":
                return m.get("id")
        return None
    except Exception as e:
        logger.warning(f"Could not query llama.cpp models: {e}")
        return None


def _llamacpp_unload(model_id: str, wait_timeout: int = 30) -> bool:
    """Unload a model from the llama.cpp router and wait until it reports unloaded."""
    try:
        base_url = _get_lmstudio_base_url()
        _http_post(f"{base_url}/models/unload", {"model": model_id})
        logger.info(f"llama.cpp: unload requested for {model_id}, waiting for VRAM release...")
    except Exception as e:
        logger.warning(f"llama.cpp unload failed: {e}")
        return False

    deadline = time.time() + wait_timeout
    while time.time() < deadline:
        try:
            base_url = _get_lmstudio_base_url()
            data = _http_get(f"{base_url}/models")
            for m in data.get("data", []):
                if m.get("id") == model_id and m.get("status", {}).get("value") == "unloaded":
                    logger.info(f"llama.cpp: {model_id} confirmed unloaded")
                    return True
        except Exception:
            pass
        time.sleep(1)

    logger.warning(f"llama.cpp: {model_id} did not confirm unload within {wait_timeout}s — proceeding anyway")
    return False


def _llamacpp_load(model_id: str) -> bool:
    """Load a model into the llama.cpp router. Returns True on success."""
    try:
        base_url = _get_lmstudio_base_url()
        _http_post(f"{base_url}/models/load", {"model": model_id})
        logger.info(f"llama.cpp: loaded {model_id}")
        return True
    except Exception as e:
        logger.warning(f"llama.cpp load failed: {e}")
        return False


# The LLM server flavor is auto-detected from the endpoint, never configured: `connector_type`
# stays the OpenAI-compatible label while the actual server is either LM Studio (native /api/v0
# REST) or a llama.cpp router (/models with per-model status). Whichever route answers wins; a
# plain single-model llama-server answers neither -> no VRAM management (its model is pinned for
# the process lifetime, so the LLM simply stays resident alongside the image model).
_llm_flavor: Optional[str] = None


def _detect_llm_flavor() -> str:
    global _llm_flavor
    if _llm_flavor is not None:
        return _llm_flavor
    base_url = _get_lmstudio_base_url()
    try:
        _http_get(f"{base_url}/api/v0/models")
        _llm_flavor = "lmstudio"
        return _llm_flavor
    except Exception:
        pass
    try:
        data = _http_get(f"{base_url}/models")
        if any("status" in (m or {}) for m in data.get("data", [])):
            _llm_flavor = "llamacpp"
            return _llm_flavor
    except Exception:
        pass
    return "none"  # not cached: re-probe next call (server may not be up yet)


def reset_llm_flavor_cache() -> None:
    """Drop the cached LLM-server flavor. Call after the LLM endpoint changes."""
    global _llm_flavor
    _llm_flavor = None


def _llm_get_loaded_model() -> Optional[str]:
    flavor = _detect_llm_flavor()
    if flavor == "lmstudio":
        return _lmstudio_get_loaded_model()
    if flavor == "llamacpp":
        return _llamacpp_get_loaded_model()
    return None


def _llm_unload(model_id: str) -> bool:
    flavor = _detect_llm_flavor()
    if flavor == "lmstudio":
        return _lmstudio_unload(model_id)
    if flavor == "llamacpp":
        return _llamacpp_unload(model_id)
    return False


def _llm_load(model_id: str) -> bool:
    flavor = _detect_llm_flavor()
    if flavor == "lmstudio":
        return _lmstudio_load(model_id)
    if flavor == "llamacpp":
        return _llamacpp_load(model_id)
    return False


def _comfyui_free_vram(endpoint: str, timeout: int = 60) -> None:
    """Ask ComfyUI to release models from VRAM, then poll until VRAM usage stabilizes."""
    try:
        _http_post(f"{endpoint}/free", {"unload_models": True, "free_memory": True})
    except Exception as e:
        logger.warning(f"ComfyUI /free failed: {e}")
        return

    # Poll /system_stats until torch_vram_free stabilizes (two consecutive equal readings)
    deadline = time.time() + timeout
    prev_free = None
    while time.time() < deadline:
        time.sleep(2)
        try:
            stats = _http_get(f"{endpoint}/system_stats")
            devices = stats.get("devices", [])
            if not devices:
                break
            vram_free = devices[0].get("torch_vram_free", 0)
            if prev_free is not None and vram_free == prev_free:
                logger.info(f"ComfyUI VRAM stabilized: {vram_free // 1024 // 1024} MiB free")
                return
            prev_free = vram_free
        except Exception:
            break

    logger.warning("ComfyUI VRAM poll timed out or failed, proceeding anyway")


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


def _run_comfyui_job(endpoint: str, prompt: str, workflow_override: Optional[dict]) -> Dict[str, Any]:
    """Submit one job to ComfyUI and wait for result. No VRAM management."""
    if workflow_override is not None:
        workflow = workflow_override
    else:
        workflow = _load_workflow(_TXT2IMG_WORKFLOW_PATH)
        workflow["11"]["inputs"]["text"] = prompt
        workflow["19"]["inputs"]["seed"] = int(uuid.uuid4().int % (2**32))

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


@contextmanager
def vram_bracket():
    """Free ComfyUI/LM-Studio VRAM for the duration, reloading the LLM on exit. Lets a caller
    wrap one or more `run_jobs` passes in ONE unload/reload cycle instead of paying it per pass.
    No-op unless `comfyui.vram_management`."""
    cfg = _get_comfyui_settings()
    vram_management = cfg.get("vram_management", False)
    endpoint = _get_comfyui_endpoint()
    unloaded_model: Optional[str] = None
    if vram_management:
        _comfyui_free_vram(endpoint)
        unloaded_model = _llm_get_loaded_model()
        if unloaded_model:
            _llm_unload(unloaded_model)
    try:
        yield
    finally:
        if vram_management:
            _comfyui_free_vram(endpoint)
            if unloaded_model:
                _llm_load(unloaded_model)


def run_jobs(jobs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Run image jobs sequentially (no VRAM management — wrap in `vram_bracket`). Each job:
    {"prompt": str, "workflow_override": dict | None}.

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

    comfyui_settings = _get_comfyui_settings()
    vram_management  = comfyui_settings.get("vram_management", False)
    endpoint         = _get_comfyui_endpoint()

    unloaded_model: Optional[str] = None
    try:
        if vram_management:
            _comfyui_free_vram(endpoint)
            unloaded_model = _llm_get_loaded_model()
            if unloaded_model:
                _llm_unload(unloaded_model)

        result = _run_comfyui_job(endpoint, prompt, workflow_override)

        if vram_management:
            _comfyui_free_vram(endpoint)
            if unloaded_model:
                _llm_load(unloaded_model)

        return result

    except urllib.error.URLError as e:
        if vram_management and unloaded_model:
            _llm_load(unloaded_model)
        return {"success": False, "error": f"Cannot reach ComfyUI at {endpoint}: {e.reason}. Is ComfyUI running with --listen?"}
    except TimeoutError as e:
        if vram_management and unloaded_model:
            _llm_load(unloaded_model)
        return {"success": False, "error": str(e)}
    except Exception as e:
        if vram_management and unloaded_model:
            _llm_load(unloaded_model)
        logger.error(f"generate_image failed: {e}")
        return {"success": False, "error": str(e)}
