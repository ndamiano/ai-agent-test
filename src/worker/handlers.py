"""What a worker DOES with a claimed payload, keyed by `payload["kind"]`.

The llm queue is a verbatim forward (no kind), so its handler is one POST. The image queue is not:
ComfyUI is submit → poll history → fetch each image. Payloads carry no endpoints; the target is the
worker's own CLI config. A queue owns its GPU.

Handlers return (result, error): exactly one is non-None.
"""

import base64
import logging
import time
import urllib.parse
import uuid

logger = logging.getLogger("worker")

COMFY_POLL_TIMEOUT = 600


def _headers(extra):
    # Authorization None strips the worker token — it belongs to the platform, not the GPU box.
    return {"Authorization": None, **(extra or {})}


def _post(agent, url, headers=None, **kw):
    return agent.session.post(url, headers=_headers(headers), **kw)


def _get(agent, url, headers=None, **kw):
    return agent.session.get(url, headers=_headers(headers), **kw)


def http_passthrough(agent, payload):
    """The llm queue: forward the body verbatim to the local inference server."""
    url = f"{agent.target}{payload.get('path', '/v1/responses')}"
    r = _post(agent, url, json=payload.get("body"), timeout=900)
    if r.status_code != 200:
        return None, f"Status {r.status_code}: {r.text[:2000]}"
    return r.json(), None


def comfy_image(agent, payload):
    """Run one fully-resolved ComfyUI workflow; return every output image inline as base64.

    `uploads` (img2img init images) land on ComfyUI's input dir first — the workflow's LoadImage
    references them by name, and ComfyUI can only read what its own /upload/image accepted."""
    for up in payload.get("uploads") or []:
        r = _post(agent, f"{agent.target}/upload/image",
                  files={"image": (up["name"], base64.b64decode(up["b64"]), "image/png")},
                  data={"overwrite": "true"}, timeout=60)
        if r.status_code != 200:
            return None, f"ComfyUI /upload/image status {r.status_code}: {r.text[:2000]}"
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
    return {"prompt_id": prompt_id, "images": images}, None


def trellis_mesh(agent, payload):
    """One sprite → one textured GLB, inline as base64.

    Retry once: the 4B pipeline degrades across generates (observed: 70s/mesh early, 160s+ and
    CuMesh OOM by ~30), and a 500 is almost always that."""
    img = base64.b64decode(payload["image_b64"])
    last = ""
    for attempt in (1, 2):
        try:
            r = _post(agent, f"{agent.target}/generate", data=img, timeout=900,
                      headers={"Content-Type": "image/png"})
            if r.status_code == 200:
                # The pod's stdout is unreachable, so the server's own load/generate split rides
                # back on the job row — otherwise a cold start can only ever be inferred.
                return {"glb_b64": base64.b64encode(r.content).decode("ascii"),
                        "stage_seconds": r.headers.get("X-Stage-Seconds"),
                        "load_seconds": r.headers.get("X-Load-Seconds"),
                        "generate_seconds": r.headers.get("X-Generate-Seconds")}, None
            last = f"Status {r.status_code}: {r.text[:2000]}"
        except Exception as e:
            last = str(e)
        logger.warning("trellis generate failed (attempt %d): %s", attempt, last)
    return None, f"TRELLIS failed: {last}"


HANDLERS = {
    "http": http_passthrough,
    "comfy_image": comfy_image,
    "trellis_mesh": trellis_mesh,
}
