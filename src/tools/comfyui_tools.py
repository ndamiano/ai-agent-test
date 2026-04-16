"""ComfyUI tools for image generation"""

import json
import uuid
import time
import logging
from pathlib import Path
from typing import Dict, Any, Optional

import urllib.request
import urllib.parse
import urllib.error

logger = logging.getLogger(__name__)

_WORKFLOWS_DIR = Path(__file__).parent.parent / "config" / "workflows"
_TXT2IMG_WORKFLOW_PATH = _WORKFLOWS_DIR / "txt2img.json"


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
        return json.loads(resp.read())


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
        _http_post(f"{base_url}/api/v0/models/unload", {"identifier": model_id})
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
        _http_post(f"{base_url}/api/v0/models/load", {"identifier": model_id})
        logger.info(f"LM Studio: loaded {model_id}")
        return True
    except Exception as e:
        logger.warning(f"LM Studio load failed: {e}")
        return False


def _comfyui_free_vram(endpoint: str, wait_secs: int = 5) -> None:
    """Ask ComfyUI to release models from VRAM and wait for the release to settle."""
    try:
        _http_post(f"{endpoint}/free", {"unload_models": True, "free_memory": True})
        logger.info(f"ComfyUI VRAM freed, waiting {wait_secs}s for release to settle...")
        time.sleep(wait_secs)
    except Exception as e:
        logger.warning(f"ComfyUI /free failed: {e}")


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


def generate_image(prompt: str) -> Dict[str, Any]:
    """
    Generate an image from a text prompt using ComfyUI.

    Args:
        prompt: Text description of the image to generate

    Returns:
        Dict with success status, saved file paths, and job metadata
    """
    comfyui_settings = _get_comfyui_settings()
    vram_management = comfyui_settings.get("vram_management", False)
    endpoint = comfyui_settings.get("endpoint", "http://localhost:8188").rstrip("/")

    unloaded_model: Optional[str] = None

    try:
        # Free VRAM: unload LM Studio model before generating
        if vram_management:
            unloaded_model = _lmstudio_get_loaded_model()
            if unloaded_model:
                _lmstudio_unload(unloaded_model)

        # Build workflow with prompt filled in
        workflow = _load_workflow(_TXT2IMG_WORKFLOW_PATH)
        workflow["11"]["inputs"]["text"] = prompt
        workflow["19"]["inputs"]["seed"] = int(uuid.uuid4().int % (2**32))

        client_id = str(uuid.uuid4())
        payload = {"prompt": workflow, "client_id": client_id}

        # Queue the prompt
        queue_resp = _http_post(f"{endpoint}/prompt", payload)
        prompt_id = queue_resp.get("prompt_id")
        if not prompt_id:
            return {
                "success": False,
                "error": f"No prompt_id in response: {queue_resp}"
            }

        logger.info(f"ComfyUI job queued: {prompt_id}")

        # Wait for completion
        result = _poll_until_done(endpoint, prompt_id)

        # Extract output image metadata
        outputs = result.get("outputs", {})
        images = []
        for node_id, node_output in outputs.items():
            for img in node_output.get("images", []):
                images.append({
                    "filename": img["filename"],
                    "subfolder": img.get("subfolder", ""),
                    "type": img.get("type", "output"),
                    "url": f"{endpoint}/view?filename={urllib.parse.quote(img['filename'])}&subfolder={urllib.parse.quote(img.get('subfolder', ''))}&type={img.get('type', 'output')}"
                })

        # Download images into working directory before freeing ComfyUI memory
        saved_paths = _save_images_to_working_dir(images, endpoint)

        # Free ComfyUI VRAM after images are safely downloaded
        _comfyui_free_vram(endpoint)

        # Reload LM Studio model
        if vram_management and unloaded_model:
            _lmstudio_load(unloaded_model)

        saved_str = ", ".join(saved_paths) if saved_paths else "(none saved)"
        return {
            "success": True,
            "prompt_id": prompt_id,
            "prompt": prompt,
            "images": images,
            "image_count": len(images),
            "saved_paths": saved_paths,
            "message": f"Image generated successfully. Saved to: {saved_str}"
        }

    except urllib.error.URLError as e:
        if vram_management and unloaded_model:
            _lmstudio_load(unloaded_model)
        return {
            "success": False,
            "error": f"Cannot reach ComfyUI at {endpoint}: {e.reason}. Is ComfyUI running with --listen?"
        }
    except TimeoutError as e:
        if vram_management and unloaded_model:
            _lmstudio_load(unloaded_model)
        return {"success": False, "error": str(e)}
    except Exception as e:
        if vram_management and unloaded_model:
            _lmstudio_load(unloaded_model)
        logger.error(f"generate_image failed: {e}")
        return {"success": False, "error": str(e)}


def register_comfyui_tools() -> None:
    from tools.tool_manager import tool_manager

    tool_manager.register_tool(
        name="generate_image",
        description=(
            "Generate an image from a text prompt using ComfyUI. "
            "Returns URLs and filenames for the generated image(s). "
            "Use this when asked to create, draw, or visualize anything."
        ),
        parameters={
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": "Detailed text description of the image to generate"
                }
            },
            "required": ["prompt"]
        },
        fn=generate_image,
        auto_inject_context=False
    )

    logger.info("ComfyUI tools registered: generate_image")
