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

logger = logging.getLogger(__name__)

_WORKFLOWS_DIR = Path(__file__).parent.parent / "config" / "workflows"
_TXT2IMG_CHARACTER_WORKFLOW_PATH = _WORKFLOWS_DIR / "txt2img_character.json"
_TXT2IMG_WORKFLOW_PATH = _WORKFLOWS_DIR / "txt2img.json"

# ---------------------------------------------------------------------------
# Prompt construction helpers
# ---------------------------------------------------------------------------

# Anima (animaOfficial_preview3Base) uses a Qwen text encoder — it reads natural
# language, not Pony/booru tag salad. score_9/score_8_up etc. are Pony embeddings
# the Qwen encoder doesn't know, so prompts are plain descriptive sentences.
_STYLE_LOCK = (
    "Anime visual novel art style with clean lineart, flat cel shading, and soft colors"
)
_CHAR_COMPOSITION = (
    "Full body view of the character standing in a relaxed neutral pose, arms at their "
    "sides, facing the viewer, against a plain white background."
)
_QUALITY = "masterpiece, best quality, highly detailed"

_CHAR_NEGATIVE = (
    "low quality, blurry, distorted, extra limbs, bad anatomy, bad hands, "
    "background scenery, multiple characters, cropped, watermark, signature, text"
)

_BG_NEGATIVE = (
    "low quality, blurry, distorted, people, characters, figures, watermark, signature, text"
)

_CG_NEGATIVE = (
    "low quality, blurry, distorted, bad anatomy, plain white background, "
    "empty scene, watermark, signature, text"
)

_TITLE_CARD_NEGATIVE = (
    "low quality, blurry, distorted, watermark, signature, ui elements, hud"
)


def _build_character_prompt(char_data: dict) -> tuple[str, str]:
    description = (char_data.get("description") or char_data.get("name") or "").strip()
    positive = f"{_QUALITY}. {_STYLE_LOCK}. {description} {_CHAR_COMPOSITION}"
    return positive, _CHAR_NEGATIVE


def _build_character_workflow(base_workflow: dict, positive: str, negative: str) -> dict:
    import copy
    wf = copy.deepcopy(base_workflow)
    wf["11"]["inputs"]["text"] = positive
    wf["12"]["inputs"]["text"] = negative
    wf["28"]["inputs"]["width"] = 832
    wf["28"]["inputs"]["height"] = 1216
    wf["19"]["inputs"]["seed"] = int(uuid.uuid4().int % (2**32))
    wf["19"]["inputs"]["cfg"] = 5.0
    wf["19"]["inputs"]["steps"] = 28
    return wf


def _build_widescreen_workflow(base_workflow: dict, positive: str, negative: str) -> dict:
    import copy
    wf = copy.deepcopy(base_workflow)
    wf["11"]["inputs"]["text"] = positive
    wf["12"]["inputs"]["text"] = negative
    wf["28"]["inputs"]["width"] = 1280
    wf["28"]["inputs"]["height"] = 720
    wf["19"]["inputs"]["seed"] = int(uuid.uuid4().int % (2**32))
    wf["19"]["inputs"]["cfg"] = 6.0
    wf["19"]["inputs"]["steps"] = 30
    return wf


def build_character_job(char_data: dict) -> dict:
    """Return a {prompt, workflow_override} job dict for a character portrait."""
    positive, negative = _build_character_prompt(char_data)
    return {
        "prompt": positive,
        "workflow_override": _build_character_workflow(_load_workflow(_TXT2IMG_CHARACTER_WORKFLOW_PATH), positive, negative),
    }


def build_background_job(description: str) -> dict:
    """Return a {prompt, workflow_override} job dict for a background image."""
    positive = (
        f"A wide, atmospheric visual novel background establishing shot of a detailed "
        f"environment, with no characters present. {description}"
    )
    return {
        "prompt": positive,
        "workflow_override": _build_widescreen_workflow(_load_workflow(_TXT2IMG_WORKFLOW_PATH), positive, _BG_NEGATIVE),
    }


def build_cg_job(description: str) -> dict:
    """Return a {prompt, workflow_override} job dict for a full-screen CG illustration."""
    positive = (
        f"{_QUALITY}. {_STYLE_LOCK}. A full-scene anime visual novel CG illustration with "
        f"characters in their environment, dynamic composition, and dramatic lighting. {description}"
    )
    wf = _build_widescreen_workflow(_load_workflow(_TXT2IMG_WORKFLOW_PATH), positive, _CG_NEGATIVE)
    wf["19"]["inputs"]["cfg"] = 7.0
    wf["19"]["inputs"]["steps"] = 35
    return {"prompt": positive, "workflow_override": wf}


def build_title_card_job(description: str) -> dict:
    """Return a {prompt, workflow_override} job dict for the game title card."""
    positive = (
        f"{_QUALITY}. {_STYLE_LOCK}. A wide, cinematic visual novel title card key visual "
        f"with an atmospheric composition. {description}"
    )
    wf = _build_widescreen_workflow(_load_workflow(_TXT2IMG_WORKFLOW_PATH), positive, _TITLE_CARD_NEGATIVE)
    wf["19"]["inputs"]["cfg"] = 7.0
    wf["19"]["inputs"]["steps"] = 35
    return {"prompt": positive, "workflow_override": wf}


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


def _http_post_empty(url: str) -> None:
    """POST with no body, ignore response."""
    req = urllib.request.Request(url, data=b"", headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30):
        pass


def _http_get(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=30) as resp:
        return json.loads(resp.read())


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


def generate_images_batch(jobs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Generate multiple images with a single LLM unload/reload cycle.

    Each job: {"prompt": str, "workflow_override": dict | None}
    Returns results in the same order as jobs.
    """
    comfyui_settings = _get_comfyui_settings()
    vram_management  = comfyui_settings.get("vram_management", False)
    endpoint         = _get_comfyui_endpoint()

    unloaded_model: Optional[str] = None
    if vram_management:
        _comfyui_free_vram(endpoint)
        unloaded_model = _lmstudio_get_loaded_model()
        if unloaded_model:
            _lmstudio_unload(unloaded_model)

    results = []
    try:
        for job in jobs:
            try:
                result = _run_comfyui_job(endpoint, job["prompt"], job.get("workflow_override"))
            except Exception as e:
                logger.error(f"generate_images_batch job failed: {e}")
                result = {"success": False, "error": str(e)}
            results.append(result)
    finally:
        if vram_management:
            _comfyui_free_vram(endpoint)
            if unloaded_model:
                _lmstudio_load(unloaded_model)

    return results


@tool_manager.tool(
    description="Generate an image from a text prompt using ComfyUI. Returns URLs and filenames for the generated image(s). Use this when asked to create, draw, or visualize anything.",
    auto_inject_context=False,
)
def generate_image(prompt: str, workflow_override: Optional[dict] = None) -> Dict[str, Any]:
    comfyui_settings = _get_comfyui_settings()
    vram_management  = comfyui_settings.get("vram_management", False)
    endpoint         = _get_comfyui_endpoint()

    unloaded_model: Optional[str] = None
    try:
        if vram_management:
            _comfyui_free_vram(endpoint)
            unloaded_model = _lmstudio_get_loaded_model()
            if unloaded_model:
                _lmstudio_unload(unloaded_model)

        result = _run_comfyui_job(endpoint, prompt, workflow_override)

        if vram_management:
            _comfyui_free_vram(endpoint)
            if unloaded_model:
                _lmstudio_load(unloaded_model)

        return result

    except urllib.error.URLError as e:
        if vram_management and unloaded_model:
            _lmstudio_load(unloaded_model)
        return {"success": False, "error": f"Cannot reach ComfyUI at {endpoint}: {e.reason}. Is ComfyUI running with --listen?"}
    except TimeoutError as e:
        if vram_management and unloaded_model:
            _lmstudio_load(unloaded_model)
        return {"success": False, "error": str(e)}
    except Exception as e:
        if vram_management and unloaded_model:
            _lmstudio_load(unloaded_model)
        logger.error(f"generate_image failed: {e}")
        return {"success": False, "error": str(e)}


