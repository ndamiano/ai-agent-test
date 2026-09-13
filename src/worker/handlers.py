"""What a worker DOES with a claimed payload, keyed by `payload["kind"]`.

The llm queue takes a CANONICAL chat request and translates it for whatever the worker's target
speaks (`--api`), so the control plane never encodes a backend's dialect. The image queue is not:
ComfyUI is submit → poll history → fetch each image. Payloads carry no endpoints; the target is the
worker's own CLI config. A queue owns its GPU.

Handlers return (result, error): exactly one is non-None.
"""

import base64
import logging
import os
import time
import urllib.parse
import uuid

from worker import safety_vision

logger = logging.getLogger("worker")

COMFY_POLL_TIMEOUT = 600
# The twelve loop clips queue behind the turn; each wait is for one prompt.
ANIM_POLL_TIMEOUT = 1800


def _headers(extra):
    # Authorization None strips the worker token — it belongs to the platform, not the GPU box.
    return {"Authorization": None, **(extra or {})}


def _post(agent, url, headers=None, **kw):
    return agent.session.post(url, headers=_headers(headers), **kw)


def _get(agent, url, headers=None, **kw):
    return agent.session.get(url, headers=_headers(headers), **kw)


def llm(agent, payload):
    """The llm queue. The request arrives CANONICAL (OpenAI chat shape); this translates it into
    whatever dialect `agent.api` says the local server speaks, and translates the reply back.

    Adding an engine is a branch in this function plus an `--api` value."""
    from llm_clients import wire
    body = payload.get("body") or {}
    if agent.api == "responses":
        r = _post(agent, f"{agent.target}/v1/responses",
                  json=wire.chat_to_responses_body(body), timeout=900)
        if r.status_code != 200:
            return None, f"Status {r.status_code}: {r.text[:2000]}"
        return wire.responses_to_chat(r.json()), None
    r = _post(agent, f"{agent.target}/v1/chat/completions",
              json=wire.chat_body_for_wire(body), timeout=900)
    if r.status_code != 200:
        return None, f"Status {r.status_code}: {r.text[:2000]}"
    return r.json(), None


def _comfy_upload(agent, name, data):
    r = _post(agent, f"{agent.target}/upload/image",
              files={"image": (name, data, "image/png")}, data={"overwrite": "true"}, timeout=60)
    if r.status_code != 200:
        raise ComfyError(f"ComfyUI /upload/image status {r.status_code}: {r.text[:2000]}")


def _comfy_submit(agent, workflow):
    r = _post(agent, f"{agent.target}/prompt",
              json={"prompt": workflow, "client_id": str(uuid.uuid4())}, timeout=60)
    if r.status_code != 200:
        raise ComfyError(f"ComfyUI /prompt status {r.status_code}: {r.text[:2000]}")
    prompt_id = r.json().get("prompt_id")
    if not prompt_id:
        raise ComfyError(f"ComfyUI returned no prompt_id: {r.text[:500]}")
    return prompt_id


def _comfy_wait(agent, prompt_id, timeout):
    deadline = time.time() + timeout
    while time.time() < deadline:
        h = _get(agent, f"{agent.target}/history/{prompt_id}", timeout=30).json()
        if prompt_id in h:
            return h[prompt_id]
        time.sleep(1)
    raise ComfyError(f"ComfyUI job {prompt_id} did not finish within {timeout}s")


def _comfy_outputs(agent, history):
    """Every output image of a finished prompt as (filename, bytes), in the order ComfyUI lists
    them — a video decoded through SaveImage lists its frames in order."""
    out = []
    for node_output in (history.get("outputs") or {}).values():
        for img in node_output.get("images", []):
            q = urllib.parse.urlencode({"filename": img["filename"],
                                        "subfolder": img.get("subfolder", ""),
                                        "type": img.get("type", "output")})
            got = _get(agent, f"{agent.target}/view?{q}", timeout=120)
            if got.status_code != 200:
                raise ComfyError(f"ComfyUI /view status {got.status_code} for {img['filename']}")
            out.append((img["filename"], got.content))
    return out


class ComfyError(Exception):
    pass


def comfy_image(agent, payload):
    """Run one fully-resolved ComfyUI workflow; return every output image inline as base64.

    `uploads` (img2img init images) land on ComfyUI's input dir first — the workflow's LoadImage
    references them by name, and ComfyUI can only read what its own /upload/image accepted."""
    try:
        for up in payload.get("uploads") or []:
            _comfy_upload(agent, up["name"], base64.b64decode(up["b64"]))
        prompt_id = _comfy_submit(agent, payload["workflow"])
        history = _comfy_wait(agent, prompt_id, COMFY_POLL_TIMEOUT)
        outputs = _comfy_outputs(agent, history)
    except ComfyError as e:
        return None, str(e)
    # Every image carries a safety verdict — scores only; the control plane's policy decides
    # what they mean, and refuses a save that arrives without them.
    images = [{"filename": name, "b64": base64.b64encode(data).decode("ascii"),
               "safety": safety_vision.classify(data)} for name, data in outputs]
    return {"prompt_id": prompt_id, "images": images}, None


def anim_sheet(agent, payload):
    """One still → one sprite sheet of the animations the build named, via MiniMax
    image-to-video. A four-facing thing runs a turntable clip first for the other three stills;
    each (animation, direction) is its own pinned clip, all queued at once so the model stays
    loaded between them. The frames come back as PNGs (the workflow
    ends in SaveImage, so no video decoding happens anywhere) and `worker/anim_sheet.py` turns
    them into the sheet. The safety verdict is the worst of the facing stills — every frame
    descends from one of them."""
    import io

    from PIL import Image

    from worker import anim_sheet as sheets
    job = uuid.uuid4().hex[:8]
    still = sheets.prep_still(Image.open(io.BytesIO(base64.b64decode(payload["image_b64"]))))
    started = time.time()
    try:
        _comfy_upload(agent, f"{job}-front.png", _png(still))
        facings = {"front": 0}
        stills = {"front": still}
        if payload["turn"] is not None:
            turn = _fill(payload["loop"]["workflow"], f"{job}-front.png",
                         payload["turn"]["prompt"], payload["turn"]["length"], payload["turn"]["steps"])
            turn_frames = _frames(_comfy_outputs(agent, _comfy_wait(agent, _comfy_submit(agent, turn),
                                                                    ANIM_POLL_TIMEOUT)))
            facings = sheets.pick_facings(turn_frames)
            for d, i in facings.items():
                if d != "front":
                    stills[d] = turn_frames[i]
                    _comfy_upload(agent, f"{job}-{d}.png", _png(turn_frames[i]))
        submitted = {}
        for anim, spec in payload["anims"].items():
            for d in payload["dirs"]:
                facing = f" {sheets.FACING_PHRASES[d]}" if payload["turn"] is not None else ""
                wf = _fill(payload["loop"]["workflow"], f"{job}-{d}.png",
                           spec["prompt"] + facing, spec["frames"])
                submitted[(anim, d)] = _comfy_submit(agent, wf)
        clips = {}
        warnings = []
        for key, prompt_id in submitted.items():
            frames = _frames(_comfy_outputs(agent, _comfy_wait(agent, prompt_id, ANIM_POLL_TIMEOUT)))
            if sheets.peak_motion(frames) < sheets.WEAK_MOTION:
                warnings.append(f"{key[0]}/{key[1]}: weak motion")
            anim = payload["anims"][key[0]]
            clips[key] = [sheets.matte(f) for f in sheets.sample(sheets.trim_pinned(frames), anim["cells"])]
    except ComfyError as e:
        return None, str(e)
    sheet, manifest = sheets.pack(clips, {a: {"fps": s["fps"]} for a, s in payload["anims"].items()},
                                  dirs=payload["dirs"], warnings=warnings)
    verdicts = [safety_vision.classify(_png(im)) for im in stills.values()]
    return {"sheet_b64": base64.b64encode(_png(sheet)).decode("ascii"),
            "manifest": manifest,
            "facings": facings,
            "safety": _worst(verdicts),
            "generate_seconds": round(time.time() - started, 1)}, None


def _png(im):
    import io
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


def _frames(outputs):
    import io

    from PIL import Image
    return [Image.open(io.BytesIO(data)).convert("RGB") for _, data in outputs]


def _fill(workflow, still_name, prompt, length, steps=None):
    import copy
    wf = copy.deepcopy(workflow)
    wf["4"]["inputs"]["image"] = still_name
    wf["5"]["inputs"]["prompt"] = prompt
    wf["5"]["inputs"]["length"] = length
    if steps is not None:
        wf["9"]["inputs"]["steps"] = steps
    wf["10"]["inputs"]["noise_seed"] = int(uuid.uuid4().int % (2**32))
    return wf


def _worst(verdicts):
    """The verdict with the highest NSFW score, so one bad facing refuses the whole sheet; a
    verdict without scores is worst of all, because the control plane refuses those outright."""
    def rank(v):
        scores = (v or {}).get("scores")
        return 2.0 if not scores else scores.get("NSFW", 1.0)
    return max(verdicts, key=rank)


def trellis_mesh(agent, payload):
    """One sprite → one textured GLB, inline as base64.

    Retry once: the 4B pipeline degrades across generates (observed: 70s/mesh early, 160s+ and
    CuMesh OOM by ~30), and a 500 is almost always that."""
    img = base64.b64decode(payload["image_b64"])
    last = ""
    for attempt in (1, 2):
        try:
            r = _post(agent, f"{agent.target}/generate", data=img, timeout=900,
                      params={"seed": payload["seed"]} if payload.get("seed") is not None else None,
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


def sprite_sheet(agent, payload):
    url = os.environ.get("SPRITE_URL") or agent.target.rsplit(":", 1)[0] + ":8190"
    try:
        r = _post(agent, f"{url}/sheet", json=payload, timeout=1800)
    except Exception as e:                                          # noqa: BLE001
        return None, f"sprite server unreachable: {e}"
    if r.status_code != 200:
        return None, f"Status {r.status_code}: {r.text[:2000]}"
    out = r.json()
    if not out.get("fallback"):
        out["safety"] = _worst([safety_vision.classify(_png(cell)) for cell in _cells(out)]
                               or [{"error": "the sheet had no cells to score"}])
    return out, None


def _cells(sheet: dict):
    import io

    from PIL import Image
    try:
        im = Image.open(io.BytesIO(base64.b64decode(sheet["sheet_b64"]))).convert("RGB")
    except Exception:
        return []
    cell = (sheet.get("manifest") or {}).get("cell") or {}
    w, h = int(cell.get("w") or 0), int(cell.get("h") or 0)
    if not w or not h:
        return [im]
    return [im.crop((x, y, x + w, y + h))
            for y in range(0, im.height - h + 1, h) for x in range(0, im.width - w + 1, w)]


HANDLERS = {
    "llm": llm,
    "comfy_image": comfy_image,
    "trellis_mesh": trellis_mesh,
    "anim_sheet": anim_sheet,
    "sprite_sheet": sprite_sheet,
}
