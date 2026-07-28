"""The asset stage: render the art the game asked for.

The GAME declares its own art. It writes `game/assets.json` —

    {"images": [{"id": "goblin", "file": "assets/goblin.png", "prompt": "a snarling goblin ..."},
                {"id": "hut", "file": "assets/hut.glb", "kind": "mesh", "prompt": "a thatched hut"}]}

— and renders each entry with a fallback for when the file is missing, so a game with no art still
plays. This module turns that manifest into real files through the same queue everything else uses:
one `image` job per entry, enqueued all at once as a batch, `asset_chain` finalizing when the last
one lands. A mesh entry chains image → TRELLIS.

Nothing here plans, rewrites or inspects the game's source. The manifest is the whole contract, which
is what makes the stage free: the model already knew what art it wanted while it was writing the code.
"""

from __future__ import annotations

import json
import logging
import threading
import uuid
from pathlib import Path
from typing import Dict, List, Optional

from PIL import Image

from db import store as db_store
from maestro.codegen.staging import game_dir
from maestro.state import RunState
from tools.comfyui_tools import build_item_payload

logger = logging.getLogger(__name__)

MANIFEST = "assets.json"

_active: set = set()
_active_lock = threading.Lock()


class AlreadyRendering(Exception):
    """A batch for this run is already in flight."""


def manifest_path(run_dir) -> Path:
    return game_dir(run_dir) / MANIFEST


def read_manifest(run_dir) -> List[Dict]:
    """The manifest's image entries, or [] when there is no manifest or it is malformed. A game
    writing junk here loses its art, never its build."""
    p = manifest_path(run_dir)
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        logger.warning("assets: %s is not valid JSON", p)
        return []
    entries = data.get("images") if isinstance(data, dict) else data
    if not isinstance(entries, list):
        return []
    return [e for e in entries
            if isinstance(e, dict) and e.get("id") and isinstance(e.get("prompt"), str)]


def asset_path(run_id: str, asset_id: str, ext: str) -> Path:
    d = game_dir(RunState(run_id).run_dir) / "assets"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{asset_id}.{ext}"


def autocrop(path: Path, pad_frac: float = 0.06) -> None:
    """Tighten a matted sprite to its opaque subject. ComfyUI renders on a 1024 frame with wide
    transparent margins, so a sprite drawn at a small on-screen size shows the subject at a fraction
    of its box — cropping to the alpha bbox (plus a small margin) makes it fill the box."""
    im = Image.open(path).convert("RGBA")
    bbox = im.split()[-1].getbbox()
    if not bbox:
        return
    pad = int(max(im.width, im.height) * pad_frac)
    box = (max(0, bbox[0] - pad), max(0, bbox[1] - pad),
           min(im.width, bbox[2] + pad), min(im.height, bbox[3] + pad))
    im.crop(box).save(path)


def _pending(run_id: str, run_dir, entries: List[Dict]) -> List[Dict]:
    """Entries whose file isn't on disk yet — so a re-run tops up rather than re-paying for art
    that already rendered."""
    out = []
    for e in entries:
        ext = "glb" if e.get("kind") == "mesh" else "png"
        if not asset_path(run_id, e["id"], ext).exists():
            out.append(e)
    return out


def start_from_manifest(run_id: str, run_dir, build_id: Optional[str] = None) -> Optional[str]:
    """Enqueue every missing asset at once and return the batch id.

    `None` means "no manifest yet, ask again next turn"; `""` means settled with nothing to do.
    NOTHING WAITS: each job carries what follows it (for a mesh, the TRELLIS job its image feeds)
    and the batch's finalize, so the stage lives in the queue where its depth is real work the
    scaler can act on."""
    entries = read_manifest(run_dir)
    if not entries:
        return None
    pending = _pending(run_id, run_dir, entries)
    if not pending:
        return ""
    with _active_lock:
        if run_id in _active:
            return ""
        _active.add(run_id)
    try:
        return _enqueue_batch(run_id, pending, build_id)
    finally:
        with _active_lock:
            _active.discard(run_id)


def _enqueue_batch(run_id: str, entries: List[Dict], build_id: Optional[str]) -> str:
    batch_id = uuid.uuid4().hex[:16]
    enqueued = 0
    for e in entries:
        payload = build_item_payload(e["prompt"])
        if payload is None:
            logger.warning("assets %s: %s blocked by the safety filter — not sent", run_id, e["id"])
            continue
        mesh = e.get("kind") == "mesh"
        then = ({"enqueue": "mesh_from_image", "finalize": "assets"} if mesh
                else {"operations": ["save_sprite"], "finalize": "assets"})
        try:
            db_store.enqueue_job("image", payload, game_id=run_id, build_id=build_id,
                                 batch_id=batch_id,
                                 metadata={"run_id": run_id, "asset_id": e["id"],
                                           "kind": e.get("kind") or "image", "then": then})
        except db_store.InsufficientCompute as err:
            logger.error("assets %s: budget refused after %d job(s): %s", run_id, enqueued, err)
            break
        enqueued += 1
    logger.info("assets %s: enqueued %d/%d job(s) as batch %s",
                run_id, enqueued, len(entries), batch_id)
    return batch_id if enqueued else ""


def add_assets(run_id: str, build_id: Optional[str] = None) -> Dict:
    """The manual re-render (the API's `assets` action): top up whatever the manifest declares and
    the run doesn't have yet."""
    state = RunState(run_id)
    entries = read_manifest(state.run_dir)
    if not entries:
        return {"ok": False, "error": "the game declares no assets.json", "batch_id": ""}
    if db_store.has_active_batch(run_id):
        raise AlreadyRendering(run_id)
    batch = start_from_manifest(run_id, state.run_dir, build_id) or ""
    return {"ok": bool(batch), "batch_id": batch, "planned": len(entries)}


def regenerate_asset(run_id: str, asset_id: str, note: str, mode: str = "full") -> Dict:
    """Re-render ONE asset as a one-job batch, reusing the same finalize. The user's text is a CHANGE
    NOTE, not the finished prompt: it is merged with the entry's ORIGINAL prompt so "give him a red
    cape" keeps the goblin. `img2img` seeds the render from the existing image, keeping composition."""
    import base64

    state = RunState(run_id)
    entry = next((e for e in read_manifest(state.run_dir) if e["id"] == asset_id), None)
    if entry is None:
        return {"ok": False, "error": f"no asset {asset_id!r} in the manifest"}
    prompt = _merge_prompt(entry["prompt"], note)
    init_b64 = None
    if mode == "img2img":
        mesh = entry.get("kind") == "mesh"
        src = asset_path(run_id, asset_id, "src.png" if mesh else "png")
        if src.exists():
            init_b64 = base64.b64encode(src.read_bytes()).decode("ascii")
    payload = build_item_payload(prompt, init_image_b64=init_b64)
    if payload is None:
        return {"ok": False, "error": "the prompt was blocked by the safety filter"}
    mesh = entry.get("kind") == "mesh"
    then = ({"enqueue": "mesh_from_image", "finalize": "assets"} if mesh
            else {"operations": ["save_sprite"], "finalize": "assets"})
    batch_id = uuid.uuid4().hex[:16]
    db_store.enqueue_job("image", payload, game_id=run_id, batch_id=batch_id,
                         metadata={"run_id": run_id, "asset_id": asset_id,
                                   "kind": entry.get("kind") or "image", "then": then})
    return {"ok": True, "batch_id": batch_id, "prompt": prompt}


def _merge_prompt(original: str, note: str) -> str:
    """One small LLM call to fold a change note into the original prompt. Falls back to appending —
    a merge that fails must not cost the user their regenerate."""
    from llm_clients.connector import get_connector
    from llm_clients.message_builder import MessageBuilder
    system = ("You rewrite image prompts. Given the ORIGINAL prompt and a CHANGE the user asked "
              "for, output the full revised prompt and nothing else. Keep everything the change "
              "does not touch.")
    user = f"ORIGINAL: {original}\n\nCHANGE: {note}"
    try:
        reply = get_connector().generate_with_tools(
            MessageBuilder(system).add_user(user).build(), [], max_tokens=300)
        text = ((reply.get("choices") or [{}])[0].get("message", {}) or {}).get("content") or ""
        merged = text.strip().strip('"')
        if merged:
            return merged
    except Exception:
        logger.exception("prompt merge failed for %r", note)
    return f"{original}, {note}"
