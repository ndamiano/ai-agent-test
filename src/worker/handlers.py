"""What a worker DOES with a claimed payload, keyed by `payload["kind"]`.

The llm queue is a verbatim forward (no kind), so its handler is one POST. The image and mesh
queues are not: ComfyUI is submit → poll history → fetch each image, and both backends need the
GPU to themselves, so the VRAM juggling that used to run control-plane-side lives here — next to
the GPU it is actually juggling. Payloads carry no endpoints; every target is the worker's own
CLI config.

Handlers return (result, error): exactly one is non-None.
"""

import base64
import logging
import time
import urllib.parse
import uuid

logger = logging.getLogger("worker")

COMFY_POLL_TIMEOUT = 600
TRELLIS_UNLOAD_EVERY = 10


def _headers(extra):
    # Authorization None strips the worker token — it belongs to the platform, not the GPU box.
    return {"Authorization": None, **(extra or {})}


def _post(agent, url, headers=None, **kw):
    return agent.session.post(url, headers=_headers(headers), **kw)


def _get(agent, url, headers=None, **kw):
    return agent.session.get(url, headers=_headers(headers), **kw)


def _evict_llm(agent) -> None:
    """Unload whatever the llama.cpp router has resident — the 30B and an image/mesh pipeline
    cannot co-reside on one card. The router reloads on demand for the next llm job."""
    if not agent.llm_target:
        return
    try:
        r = _get(agent, f"{agent.llm_target}/models", timeout=10)
        for m in (r.json().get("data") or []):
            if m.get("state") in ("loaded", "loading") or m.get("loaded"):
                _post(agent, f"{agent.llm_target}/models/unload",
                      json={"model": m["id"]}, timeout=60)
                logger.info("evicted LLM %s from the GPU", m["id"])
    except Exception as e:
        logger.warning("LLM eviction skipped: %s", e)


def _free_comfy(agent) -> None:
    try:
        _post(agent, f"{agent.comfy_target}/free",
              json={"unload_models": True, "free_memory": True}, timeout=60)
    except Exception as e:
        logger.warning("ComfyUI VRAM free skipped: %s", e)


def http_passthrough(agent, payload):
    """The llm queue: forward the body verbatim to the local inference server."""
    url = f"{agent.target}{payload.get('path', '/v1/responses')}"
    r = _post(agent, url, json=payload.get("body"), timeout=900)
    if r.status_code != 200:
        return None, f"Status {r.status_code}: {r.text[:2000]}"
    return r.json(), None


def comfy_image(agent, payload):
    """Run one fully-resolved ComfyUI workflow; return every output image inline as base64."""
    _free_comfy(agent)
    _evict_llm(agent)

    r = _post(agent, f"{agent.target}/prompt",
              json={"prompt": payload["workflow"], "client_id": str(uuid.uuid4())}, timeout=60)
    if r.status_code != 200:
        return None, f"ComfyUI /prompt status {r.status_code}: {r.text[:2000]}"
    prompt_id = r.json().get("prompt_id")
    if not prompt_id:
        return None, f"ComfyUI returned no prompt_id: {r.text[:500]}"

    deadline = time.time() + COMFY_POLL_TIMEOUT
    history = None
    while time.time() < deadline:
        h = _get(agent, f"{agent.target}/history/{prompt_id}", timeout=30).json()
        if prompt_id in h:
            history = h[prompt_id]
            break
        time.sleep(1)
    if history is None:
        return None, f"ComfyUI job {prompt_id} did not finish within {COMFY_POLL_TIMEOUT}s"

    images = []
    for node_output in (history.get("outputs") or {}).values():
        for img in node_output.get("images", []):
            q = urllib.parse.urlencode({"filename": img["filename"],
                                        "subfolder": img.get("subfolder", ""),
                                        "type": img.get("type", "output")})
            got = _get(agent, f"{agent.target}/view?{q}", timeout=120)
            if got.status_code != 200:
                return None, f"ComfyUI /view status {got.status_code} for {img['filename']}"
            images.append({"filename": img["filename"],
                           "b64": base64.b64encode(got.content).decode("ascii")})
    _free_comfy(agent)
    return {"prompt_id": prompt_id, "images": images}, None


def trellis_mesh(agent, payload):
    """One sprite → one textured GLB, inline as base64.

    The resident 4B pipeline leaks VRAM across generates (observed: 70s/mesh early, 160s+ and
    CuMesh OOM by ~30), so unload periodically; a 500 is almost always that degradation, so
    unload and retry once before giving up."""
    _free_comfy(agent)
    _evict_llm(agent)
    agent.mesh_count = getattr(agent, "mesh_count", 0) + 1
    if agent.mesh_count % TRELLIS_UNLOAD_EVERY == 0:
        _trellis_unload(agent)

    img = base64.b64decode(payload["image_b64"])
    last = ""
    for attempt in (1, 2):
        try:
            r = _post(agent, f"{agent.target}/generate", data=img, timeout=900,
                      headers={"Content-Type": "image/png"})
            if r.status_code == 200:
                return {"glb_b64": base64.b64encode(r.content).decode("ascii")}, None
            last = f"Status {r.status_code}: {r.text[:2000]}"
        except Exception as e:
            last = str(e)
        logger.warning("trellis generate failed (attempt %d): %s", attempt, last)
        if attempt == 1:
            _trellis_unload(agent)
    return None, f"TRELLIS failed: {last}"


def _trellis_unload(agent) -> None:
    try:
        _post(agent, f"{agent.target}/unload", data=b"", timeout=60,
              headers={"Content-Type": "application/json"})
    except Exception as e:
        logger.warning("TRELLIS unload skipped: %s", e)


HANDLERS = {
    "http": http_passthrough,
    "comfy_image": comfy_image,
    "trellis_mesh": trellis_mesh,
}
