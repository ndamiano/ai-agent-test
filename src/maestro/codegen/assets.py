"""The asset stage: render the art the game asked for.

The GAME asks for its own art, mid-build, by calling `generate_media` — one call per image or mesh,
answered immediately with the path the file will appear at. `request_media` is what that tool runs:
it enqueues one `image` job (a mesh chains image → TRELLIS) and RECORDS the request in
`game/assets.json` —

    {"images": [{"id": "goblin", "file": "assets/goblin.png", "prompt": "a snarling goblin ..."},
                {"id": "hut", "file": "assets/hut.glb", "kind": "mesh", "prompt": "a thatched hut"}]}

The model never writes that file. It is the durable RECORD of what was asked for: what the gallery
lists, what a top-up re-renders, what a regenerate re-prompts against. Nothing here plans, rewrites
or inspects the game's source.

One request is one BATCH, so each asset finalizes on its own and reaches `/play` as it lands rather
than when the slowest render in a set does.
"""

from __future__ import annotations

import base64
import json
import logging
import re
import threading
import uuid
from pathlib import Path
from typing import Dict, List, Optional

from PIL import Image

from db import store as db_store
from maestro.codegen.staging import game_dir
from maestro.state import RunState
from tools.comfyui_tools import MATTED_KINDS, build_image_payload
from tools.execution_context import run_scope

logger = logging.getLogger(__name__)

MANIFEST = "assets.json"

# What the game can ask for. A sprite sits ON the game's background, a tile and a scene ARE one,
# and a mesh renders as a sprite first because TRELLIS turns a matted subject into geometry.
KINDS = ("sprite", "tile", "scene", "mesh")
DEFAULT_KIND = "sprite"

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


def render_kind(kind: str) -> str:
    """The kind the IMAGE leg renders as. A mesh's image is the subject TRELLIS lifts into
    geometry, so it is drawn and matted exactly like a sprite."""
    return "sprite" if kind == "mesh" else kind


def check_render(path: Path, kind: str) -> Optional[str]:
    """A defect in a finished render, or None. BROKEN only — nothing here has an opinion about
    whether the picture is any good, because code cannot have one.

    What it can read is the alpha channel against what the kind asked for. A matted sprite that
    came back fully opaque is a scene the matte never cut, and the game will composite a square of
    someone else's background onto its own; one that came back empty is a subject the matte ate.
    A tile or a scene is the reverse — it fills its frame, so transparency in one is a hole."""
    im = Image.open(path).convert("RGBA")
    total = im.width * im.height
    if not total:
        return "the render is empty"
    hist = im.split()[-1].histogram()
    opaque = sum(hist[200:]) / total
    if kind in MATTED_KINDS:
        if opaque > 0.98:
            return ("the background was not removed — this render is a full opaque square, so it "
                    "will show its own background wherever the game draws it")
        if opaque < 0.02:
            return "almost nothing is left after the background was removed"
    elif opaque < 0.9:
        return f"a {kind} must fill its frame, and {(1 - opaque) * 100:.0f}% of this one is transparent"
    return None


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


# An id names a file and is handed back to the asset routes, so it carries only what both accept.
_MEDIA_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def _record(run_dir, entry: Dict) -> None:
    """Append one requested asset to the manifest, AFTER its enqueue lands — the file records what
    was actually asked of the queue."""
    p = manifest_path(run_dir)
    data = {}
    if p.exists():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            data = {}
    images = data.get("images") if isinstance(data, dict) else None
    if not isinstance(images, list):
        images = []
    data = {"images": images + [entry]}
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def _update(run_dir, asset_id: str, **fields) -> None:
    """Rewrite one manifest entry in place. `None` drops the key."""
    p = manifest_path(run_dir)
    entries = read_manifest(run_dir)
    for e in entries:
        if e["id"] == asset_id:
            for k, v in fields.items():
                e.pop(k, None) if v is None else e.update({k: v})
    p.write_text(json.dumps({"images": entries}, indent=2, ensure_ascii=False), encoding="utf-8")


def set_defect(run_dir, asset_id: str, defect: Optional[str]) -> None:
    """Record (or clear) what a landed render came back broken as."""
    _update(run_dir, asset_id, defect=defect)


def entry_kind(entry: Dict) -> str:
    return entry.get("kind") or DEFAULT_KIND


def _then_for(kind: str) -> Dict:
    """What a finished render owes. A mesh's image leg chains TRELLIS; a matted kind is cropped to
    its subject on the way in and an unmatted one must not be, since cropping a tile to its
    "subject" is how a floor becomes a handful of planks."""
    if kind == "mesh":
        return {"enqueue": "mesh_from_image", "finalize": "assets"}
    op = "save_sprite" if render_kind(kind) in MATTED_KINDS else "save_flat"
    return {"operations": [op], "finalize": "assets"}


def request_media(run_id: str, run_dir, asset_id: str, prompt: str,
                  kind: str = DEFAULT_KIND) -> Dict:
    """ONE asset. Enqueues the render and answers with the path the file will appear at, so the
    model can write code against it on the same turn.

    Every refusal is REPORTED: a build that cannot have art must be told to draw one rather than
    left waiting for a file that is never coming."""
    if not _MEDIA_ID.match(asset_id or ""):
        return {"ok": False, "error": "id must be 1-64 characters of letters, digits, - or _"}
    if not (prompt or "").strip():
        return {"ok": False, "error": "prompt is required: describe what to draw"}
    if kind not in KINDS:
        return {"ok": False,
                "error": f"kind must be one of {', '.join(KINDS)} — not {kind!r}"}

    mesh = kind == "mesh"
    ext = "glb" if mesh else "png"
    rel = f"assets/{asset_id}.{ext}"
    existing = next((e for e in read_manifest(run_dir) if e["id"] == asset_id), None)
    if not existing and asset_path(run_id, asset_id, ext).exists():
        return {"ok": True, "path": rel, "status": "ready"}
    if existing and not existing.get("replace_asked"):
        # An id asked for twice is usually the model losing track, so the first repeat is ANSWERED
        # rather than obeyed — the done-nudge shape. What it must not do is agree and keep the old
        # picture, which is what someone asking for the art to be redrawn used to get.
        _update(run_dir, asset_id, replace_asked=True)
        return {"ok": True, "path": rel,
                "status": "ready" if asset_path(run_id, asset_id, ext).exists() else "rendering",
                "note": "not requeued — this id already has art. Call again with the prompt you "
                        "want to replace it with, or use a different id to draw something new."}

    payload = build_image_payload(prompt, render_kind(kind))
    if payload is None:
        return {"ok": False, "error": "that prompt was refused by the safety filter — "
                                      "draw this one with code instead"}
    then = _then_for(kind)
    try:
        # No build_id: this batch's finalize would otherwise close the BUILD's row the moment the
        # first sprite lands, while the model is still writing the game.
        db_store.enqueue_job("image", payload, game_id=run_id, batch_id=uuid.uuid4().hex[:16],
                             metadata={"run_id": run_id, "asset_id": asset_id, "kind": kind,
                                       "then": then})
    except db_store.InsufficientCompute:
        return {"ok": False, "error": "no compute left for art — draw this one with code instead"}
    if existing:
        # The manifest holds the prompt a regenerate re-prompts against, so a confirmed replace
        # rewrites it rather than leaving the record describing art that no longer exists.
        _update(run_dir, asset_id, prompt=prompt, replace_asked=None, defect=None)
    else:
        _record(run_dir, {"id": asset_id, "file": rel, "kind": kind, "prompt": prompt})
    logger.info("assets %s: %s requested (%s)", run_id, asset_id, kind)
    out = {"ok": True, "path": rel, "status": "rendering"}
    if mesh:
        out["note"] = ("the model will span exactly 1 unit at its longest side — scale it in the "
                       "scene to its real-world size (person ≈ 1.7 units, building ≈ 6+).")
    return out


def start_from_manifest(run_id: str, run_dir, build_id: Optional[str] = None) -> Optional[str]:
    """Enqueue every missing asset at once and return the batch id — the TOP-UP path, for a run
    whose renders failed or were never paid for.

    `None` means there is no manifest to top up; `""` means settled with nothing to do.
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
        kind = entry_kind(e)
        mesh = kind == "mesh"
        queue = "image"
        payload = build_image_payload(e["prompt"], render_kind(kind))
        then = _then_for(kind)
        src = asset_path(run_id, e["id"], "src.png")
        if mesh and src.exists():
            # A mesh needs ComfyUI and then TRELLIS, and one GPU can only hold one of them, so a
            # top-up that always restarted at the image leg could never reach the second half.
            queue, then = "mesh", {"operations": ["decimate"], "finalize": "assets"}
            payload = {"kind": "trellis_mesh",
                       "image_b64": base64.b64encode(src.read_bytes()).decode("ascii")}
        if payload is None:
            logger.warning("assets %s: %s blocked by the safety filter — not sent", run_id, e["id"])
            continue
        try:
            db_store.enqueue_job(queue, payload, game_id=run_id, build_id=build_id,
                                 batch_id=batch_id,
                                 metadata={"run_id": run_id, "asset_id": e["id"],
                                           "kind": kind, "then": then})
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
    # The merge is a GPU job like any other, and it is the one enqueue here that goes through the
    # blocking connector, which reads the owning game off the run scope rather than an argument.
    with run_scope(run_id):
        prompt = _merge_prompt(entry["prompt"], note)
    kind = entry_kind(entry)
    init_b64 = None
    if mode == "img2img":
        src = asset_path(run_id, asset_id, "src.png" if kind == "mesh" else "png")
        if src.exists():
            init_b64 = base64.b64encode(src.read_bytes()).decode("ascii")
    payload = build_image_payload(prompt, render_kind(kind), init_image_b64=init_b64)
    if payload is None:
        return {"ok": False, "error": "the prompt was blocked by the safety filter"}
    batch_id = uuid.uuid4().hex[:16]
    db_store.enqueue_job("image", payload, game_id=run_id, batch_id=batch_id,
                         metadata={"run_id": run_id, "asset_id": asset_id,
                                   "kind": kind, "then": _then_for(kind)})
    _update(state.run_dir, asset_id, defect=None)
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
