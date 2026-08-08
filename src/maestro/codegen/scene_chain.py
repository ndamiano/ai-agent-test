"""The scene chain: compose_scene's GPU-backed path, as jobs on the existing queues.

`compose()` runs at tool time and is the SYNCHRONOUS half: one plan call (the game's llm
queue, blocking — the build turn that called the tool has already completed, so the queue
is free), the solver, the scene.json the model wires collision from, and a code-painted
ground so the game renders whether or not art ever lands. Then it enqueues the ASYNC half
and returns.

The async half is one batch per stage, and the batch finalize IS the fan-in barrier —
`claim_batch_finalize` already guarantees exactly-one execution against the reaper:

  batch 1  N subject jobs (store misses only; image queue, each chaining a TRELLIS job on
           the mesh queue whose completion deposits subject+mesh+sprite to the store)
           + 1 terrain job (image queue, masked img2img over the painted ground)
  finalize `scene_objects`: composite store sprites over the terrain -> enqueue batch 2
  batch 2  1 embedding job (image queue, Qwen-Edit)
  finalize `scene_embed`: drift-check against the composite, land the final ground, restage

Every enqueue carries the run's game_id, so admission and debit ride the queue like all GPU
work. A refused budget at any seam leaves the best ground already on disk — code paint,
then terrain, then composite, then embed, each overwriting the last AT THE SAME PATH the
game already loads.

Store hits skip the whole object leg; a claim lost to another run is treated as a hit that
hasn't landed yet — the composite uses whatever the store holds when the barrier falls, and
a type still rendering simply misses this map (the next one gets it).
"""

from __future__ import annotations

import base64
import io
import json
import logging
import random
import uuid
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
from PIL import Image

from db import store as db_store
from maestro.codegen import asset_store
from maestro.codegen.assets import render_verdict
from maestro.codegen.staging import stage_for_play
from maestro.state import RunState
from scenegen.blockout import solve
from tools.safety import screen_image_prompt

logger = logging.getLogger(__name__)

CELL = 48
GRID_W, GRID_H = 48, 36
VISUAL_SCALE = 1.4  # sprites overhang their footprint; the box stays the collision truth

_PROMPT_PATH = Path(__file__).resolve().parents[2] / "scenegen" / "prompts" / "blockout_plan.txt"
_WORKFLOWS = Path(__file__).resolve().parents[2] / "config" / "workflows"

SUBJECT_PROMPT = ("A single {phrase}, standing upright on the ground, one isolated object "
                  "centered on a plain pure white background, three-quarter view seen from "
                  "slightly above, clean video game asset, bright even studio lighting, "
                  "whole object fully visible")
SUBJECT_NEGATIVE = ("photo background, scenery, floor, ground shadow, multiple objects, "
                    "text, watermark, cropped, dark, dim lighting")

# Style words only — content nouns in a repaint prompt summon content (measured: a second
# lighthouse). The words come from the terrain the solver actually placed, never the request.
_TERRAIN_WORDS = {"grass": "lush green grass with tufts and patches",
                  "meadow": "soft green meadow grass",
                  "sand": "warm sandy ground", "snow": "fresh white snow, icy patches",
                  "dirt": "worn packed dirt", "rock": "grey rocky ground",
                  "forest": "dark forest floor, moss and leaf litter",
                  "water": "clear blue water", "lava": "glowing molten lava",
                  "plaza": "flat paved stone", "field": "tilled soil rows",
                  "road": "worn dirt paths"}
TERRAIN_NEGATIVE = ("buildings, houses, trees, people, objects, text, watermark, blurry, "
                    "photo")

# Diffusion whispers over structural cells so code keeps owning where things ARE.
_PROTECT = ("water", "lava", "road", "path", "sand", "dock", "plaza")
PROTECT_STRENGTH = 0.45

EMBED_INSTRUCTION = ("Make every object sit naturally in the terrain: ground texture "
                     "slightly overlapping the bottom edge of each building and object, "
                     "soft contact shadows, worn dirt at doorways. Keep every object "
                     "exactly where it is, the same size and the same kind of object. "
                     "Keep roads and water where they are.")

# A box whose pixels moved this far from the composite has been redrawn into something
# else by the edit pass. Detection only — the human decides what to do with a drifted map.
DRIFT_THRESHOLD = 60.0

_FLAT = {"grass": (110, 156, 92), "meadow": (128, 168, 100), "sand": (206, 188, 142),
         "snow": (226, 228, 232), "dirt": (140, 110, 86), "rock": (120, 116, 110),
         "forest": (80, 114, 66), "water": (96, 144, 178), "lava": (196, 90, 40),
         "plaza": (184, 176, 162), "field": (172, 148, 100), "road": (176, 150, 116)}


def _scene_state_path(run_dir, scene_id: str) -> Path:
    d = Path(run_dir) / "scenes"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{scene_id}.json"


def _read_state(md: Dict) -> Dict:
    return json.loads(_scene_state_path(RunState(md["run_id"]).run_dir,
                                        md["scene_id"]).read_text(encoding="utf-8"))


def _wf(name: str) -> Dict:
    return json.loads((_WORKFLOWS / name).read_text(encoding="utf-8"))


def _b64(im: Image.Image) -> str:
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


# ---------------------------------------------------------------------------
# The synchronous half.

def compose(root: Path, run_id: str, scene_id: str, archetype: str, style: str,
            seed: Optional[int], width_cells: Optional[int],
            height_cells: Optional[int]) -> Dict:
    seed = int(seed) if seed is not None else random.Random(scene_id).randint(0, 9999)
    w = min(int(width_cells or GRID_W), 64)
    h = min(int(height_cells or GRID_H), 48)
    request = f"a {archetype}: {style}" if style else f"a {archetype}"

    plan = _plan(request, seed, run_id)
    solved = solve(plan, request, seed, w, h)
    blockout, scene = solved["blockout"], solved["scene"]

    ground_rel = f"assets/{scene_id}_ground.png"
    json_rel = f"assets/{scene_id}_scene.json"
    (root / "assets").mkdir(parents=True, exist_ok=True)
    scene.update({"archetype": archetype, "cell_px": CELL, "ground": ground_rel})
    (root / json_rel).write_text(json.dumps(scene), encoding="utf-8")

    ground = _paint_ground(blockout)
    ground.save(root / ground_rel)

    state = {"run_id": run_id, "scene_id": scene_id, "style": style or "",
             "blockout": blockout, "ground_rel": ground_rel}
    _scene_state_path(RunState(run_id).run_dir, scene_id).write_text(
        json.dumps(state), encoding="utf-8")

    enqueued = _start_batch(run_id, state, ground)
    if not enqueued:
        # Nothing in flight will ever composite, but the store may already hold every
        # sprite this map needs — a whole town of known types costs zero GPU.
        composed = _composite(state, ground.convert("RGBA"))
        composed.convert("RGB").save(root / ground_rel)
    note = (f"scene built. Read {json_rel} for the walkable grid, door cells and points of "
            f"interest; draw {ground_rel} as the map background, one cell = {CELL}px.")
    if enqueued:
        note += (" The background art is still rendering and will replace the placeholder "
                 "at the same path — keep loading it from there.")
    return {"ok": True, "files": [ground_rel, json_rel], "note": note}


def _plan(request: str, seed: int, run_id: str) -> Dict:
    """One small llm call for the relations plan; a plan that fails degrades to one plaza,
    the same fallback the solver applies to a malformed dict."""
    from llm_clients.connector import get_connector
    from llm_clients.message_builder import MessageBuilder
    from tools.execution_context import run_scope
    prompt = _PROMPT_PATH.read_text(encoding="utf-8").replace("{request}", request)
    try:
        with run_scope(run_id):
            reply = get_connector().generate_with_tools(
                MessageBuilder("You output only JSON.")
                .add_user(prompt + f"\n(variation {seed})").build(), [], max_tokens=6000)
        text = ((reply.get("choices") or [{}])[0].get("message", {}) or {}).get("content") or ""
        data = asset_store._extract_json(text)
        if isinstance(data, dict):
            return data
    except Exception:
        logger.exception("scene %s: plan call failed", run_id)
    return {}


def _paint_ground(blockout: Dict) -> Image.Image:
    names = blockout["terrain_names"]
    grid = np.array([[int(ch) for ch in row] for row in blockout["terrain_grid"]])
    h, w = grid.shape
    img = np.zeros((h, w, 3), np.uint8)
    for i, name in enumerate(names):
        img[grid == i] = _FLAT.get(name, _FLAT["grass"])
    img = np.kron(img, np.ones((CELL, CELL, 1), dtype=np.uint8))
    rng = np.random.default_rng(1)
    jitter = rng.integers(-6, 7, size=(h * CELL, w * CELL, 1), dtype=np.int16)
    return Image.fromarray(np.clip(img.astype(np.int16) + jitter, 0, 255).astype(np.uint8))


def _start_batch(run_id: str, state: Dict, ground: Image.Image) -> bool:
    boxes = [b for b in state["blockout"]["boxes"]
             if b["kind"] in ("building", "landmark", "decoration")]
    resolved = asset_store.resolve_types([b["name"] for b in boxes], state["style"], run_id)
    state["resolved"] = resolved
    _scene_state_path(RunState(run_id).run_dir, state["scene_id"]).write_text(
        json.dumps(state), encoding="utf-8")

    batch_id = uuid.uuid4().hex[:16]
    md_base = {"run_id": run_id, "scene_id": state["scene_id"]}
    enqueued = 0
    for name, r in resolved.items():
        if asset_store.lookup(r["key"]) or not asset_store.claim(r["key"], run_id):
            continue
        phrase = SUBJECT_PROMPT.format(phrase=r["phrase"])
        if screen_image_prompt(r["phrase"]) is not None:
            logger.warning("scene %s: subject %r blocked — box keeps the code paint",
                           run_id, name)
            asset_store.release(r["key"])
            continue
        wf = _wf("txt2img_subject.json")
        wf["p"]["inputs"]["text"] = phrase
        wf["n"]["inputs"]["text"] = SUBJECT_NEGATIVE
        wf["k"]["inputs"]["seed"] = 3
        try:
            db_store.enqueue_job(
                "image", {"kind": "comfy_image", "workflow": wf},
                game_id=run_id, batch_id=batch_id,
                metadata={**md_base, "store_key": r["key"],
                          "then": {"enqueue": "scene_mesh_from_image",
                                   "finalize": "scene_objects"}})
            enqueued += 1
        except db_store.InsufficientCompute:
            asset_store.release(r["key"])
            logger.warning("scene %s: budget refused subjects after %d — code ground stands",
                           run_id, enqueued)
            return bool(enqueued)

    try:
        db_store.enqueue_job(
            "image", _terrain_payload(state, ground), game_id=run_id, batch_id=batch_id,
            metadata={**md_base, "role": "terrain",
                      "then": {"operations": ["scene_terrain"],
                               "finalize": "scene_objects"}})
        enqueued += 1
    except db_store.InsufficientCompute:
        logger.warning("scene %s: budget refused terrain — code ground stands", run_id)
    return bool(enqueued)


def _terrain_payload(state: Dict, ground: Image.Image) -> Dict:
    blockout = state["blockout"]
    names = blockout["terrain_names"]
    grid = np.array([[int(ch) for ch in row] for row in blockout["terrain_grid"]])
    protect = {i for i, n in enumerate(names) if any(k in n for k in _PROTECT)}
    m = np.where(np.isin(grid, list(protect)), PROTECT_STRENGTH, 1.0).astype(np.float32)
    m = np.kron(m, np.ones((CELL, CELL), dtype=np.float32))
    mask = Image.fromarray((m * 255).astype(np.uint8), "L")

    words = ", ".join(dict.fromkeys(
        _TERRAIN_WORDS[n] for n in names if n in _TERRAIN_WORDS))
    init_name = f"scene_ter_{uuid.uuid4().hex}.png"
    mask_name = f"scene_msk_{uuid.uuid4().hex}.png"
    wf = _wf("img2img_terrain.json")
    wf["li"]["inputs"]["image"] = init_name
    wf["lm"]["inputs"]["image"] = mask_name
    wf["p"]["inputs"]["text"] = (f"top-down painted game terrain, {words}, soft painterly "
                                 "texture, no objects, empty ground")
    wf["n"]["inputs"]["text"] = TERRAIN_NEGATIVE
    return {"kind": "comfy_image", "workflow": wf,
            "uploads": [{"name": init_name, "b64": _b64(ground)},
                        {"name": mask_name, "b64": _b64(mask)}]}


# ---------------------------------------------------------------------------
# The async half: continuation, operations, finalizers.

def _first_image(result: Optional[Dict]) -> Optional[Dict]:
    for img in (result or {}).get("images") or []:
        if img.get("file"):
            return img
    return None


def _scene_mesh_from_image(md: Dict, result: Dict) -> Optional[Dict]:
    """Subject landed: safety first, then it enters the STORE (not a game folder) and its
    TRELLIS job goes out. A refused subject releases the claim — the type renders again
    for the next map rather than caching a hole."""
    entry = _first_image(result)
    if entry is None:
        asset_store.release(md["store_key"])
        return None
    reason = render_verdict(entry)
    if reason is not None:
        logger.warning("scene %s: subject %s refused — %s",
                       md["run_id"], md["store_key"], reason)
        Path(entry["file"]).unlink(missing_ok=True)
        asset_store.release(md["store_key"])
        from tools.safety import SafetyViolation, log_violation
        log_violation(SafetyViolation("nsfw_render", reason), run_id=md["run_id"],
                      source="image_render")
        return None
    data = Path(entry["file"]).read_bytes()
    key = md["store_key"]
    resolved = _read_state(md).get("resolved") or {}
    phrase = next((r["phrase"] for r in resolved.values() if r["key"] == key), key)
    asset_store.deposit_subject(key, _autocropped(data), phrase, "qwen-image-2512")
    return {"queue": "mesh",
            "payload": {"kind": "trellis_mesh",
                        "image_b64": base64.b64encode(data).decode("ascii")},
            "metadata": {**{k: md[k] for k in ("run_id", "scene_id", "store_key")},
                         "then": {"operations": ["scene_mesh"],
                                  "finalize": md["then"]["finalize"]}}}


def _autocropped(png: bytes) -> bytes:
    im = Image.open(io.BytesIO(png)).convert("RGBA")
    bbox = im.split()[-1].getbbox()
    if bbox:
        im = im.crop(bbox)
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


def _scene_mesh(md: Dict, result: Dict) -> None:
    """GLB landed: deposit it, render the shared-camera sprite, complete the entry. A
    sprite render that cannot run falls back to the subject itself — a 3/4 subject is a
    worse sprite than an orthographic render and a better one than nothing."""
    src = (result or {}).get("glb_file")
    if not src:
        asset_store.release(md["store_key"])
        return
    key = md["store_key"]
    glb = Path(src).read_bytes()
    asset_store.deposit_mesh(key, glb, "trellis2")
    from tools.mesh_render import CAMERA, render_glb
    tmp = asset_store.entry_dir(key) / ".sprite_render.png"
    try:
        render_glb(glb, tmp)
        data = _autocropped(tmp.read_bytes())
        camera = CAMERA
    except Exception as e:
        logger.warning("scene sprite render %s failed, using the subject: %s", key, e)
        data = asset_store.subject_path(key).read_bytes()
        camera = "subject-fallback"
    finally:
        tmp.unlink(missing_ok=True)
    asset_store.deposit_sprite(key, data, camera)


def _scene_terrain(md: Dict, result: Dict) -> None:
    entry = _first_image(result)
    if entry is None:
        return
    if render_verdict(entry) is not None:
        Path(entry["file"]).unlink(missing_ok=True)
        return
    run_dir = RunState(md["run_id"]).run_dir
    dst = _scene_state_path(run_dir, md["scene_id"]).with_suffix(".terrain.png")
    dst.write_bytes(Path(entry["file"]).read_bytes())
    Path(entry["file"]).unlink(missing_ok=True)


def _finalize_scene_objects(md: Dict, jobs: List[Dict]) -> None:
    """The barrier fell: every subject/mesh chain and the terrain have landed (or died).
    Composite what exists and send it to the embedding pass."""
    run_id, scene_id = md["run_id"], md["scene_id"]
    state = _read_state(md)
    run_dir = RunState(run_id).run_dir
    root = _game_root(run_dir)

    terrain_p = _scene_state_path(run_dir, scene_id).with_suffix(".terrain.png")
    base = (Image.open(terrain_p).convert("RGBA") if terrain_p.exists()
            else Image.open(root / state["ground_rel"]).convert("RGBA"))
    composed = _composite(state, base)
    composed_p = _scene_state_path(run_dir, scene_id).with_suffix(".composed.png")
    composed.convert("RGB").save(composed_p)

    payload = _embed_payload(composed)
    try:
        db_store.enqueue_job("image", payload, game_id=run_id,
                             batch_id=uuid.uuid4().hex[:16],
                             metadata={"run_id": run_id, "scene_id": scene_id,
                                       "then": {"operations": ["scene_embed"],
                                                "finalize": "scene_embed"}})
    except db_store.InsufficientCompute:
        logger.warning("scene %s: budget refused the embed — composite lands as-is", run_id)
        _land_ground(run_id, state, composed.convert("RGB"))


def _composite(state: Dict, base: Image.Image) -> Image.Image:
    resolved = state.get("resolved") or {}
    boxes = [b for b in state["blockout"]["boxes"]
             if b["kind"] in ("building", "landmark", "decoration")]
    out = base.copy()
    for b in sorted(boxes, key=lambda b: b["y"] + b["h"]):
        r = resolved.get(b["name"])
        entry = r and asset_store.lookup(r["key"])
        if not entry:
            continue
        sprite = Image.open(asset_store.sprite_path(r["key"])).convert("RGBA")
        bw, bh = b["w"] * CELL, b["h"] * CELL
        scale = bw * VISUAL_SCALE / sprite.width
        sh = min(int(sprite.height * scale), int(bh * 2.0))
        sw = int(sprite.width * scale)
        resized = sprite.resize((max(1, sw), max(1, sh)), Image.LANCZOS)
        px = b["x"] * CELL + (bw - sw) // 2
        py = b["y"] * CELL + bh - sh
        out.paste(resized, (px, py), resized)
    return out


def _embed_payload(composed: Image.Image) -> Dict:
    name = f"scene_emb_{uuid.uuid4().hex}.png"
    wf = _wf("imgedit_scene.json")
    wf["li"]["inputs"]["image"] = name
    wf["p"]["inputs"]["prompt"] = EMBED_INSTRUCTION
    wf["n"]["inputs"]["prompt"] = ""
    return {"kind": "comfy_image", "workflow": wf,
            "uploads": [{"name": name, "b64": _b64(composed.convert("RGB"))}]}


def _scene_embed(md: Dict, result: Dict) -> None:
    entry = _first_image(result)
    if entry is None:
        return
    if render_verdict(entry) is not None:
        Path(entry["file"]).unlink(missing_ok=True)
        return
    run_dir = RunState(md["run_id"]).run_dir
    dst = _scene_state_path(run_dir, md["scene_id"]).with_suffix(".final.png")
    dst.write_bytes(Path(entry["file"]).read_bytes())
    Path(entry["file"]).unlink(missing_ok=True)


def _finalize_scene_embed(md: Dict, jobs: List[Dict]) -> None:
    run_id, scene_id = md["run_id"], md["scene_id"]
    state = _read_state(md)
    run_dir = RunState(run_id).run_dir
    final_p = _scene_state_path(run_dir, scene_id).with_suffix(".final.png")
    composed_p = _scene_state_path(run_dir, scene_id).with_suffix(".composed.png")
    if not final_p.exists():
        if composed_p.exists():
            _land_ground(run_id, state, Image.open(composed_p).convert("RGB"))
        return
    final = Image.open(final_p).convert("RGB")
    drifted = _drift(state, Image.open(composed_p).convert("RGB"), final) \
        if composed_p.exists() else []
    if drifted:
        logger.warning("scene %s/%s: embed drifted %d box(es): %s",
                       run_id, scene_id, len(drifted), ", ".join(drifted[:8]))
        state["drift"] = drifted
        _scene_state_path(run_dir, scene_id).write_text(json.dumps(state), encoding="utf-8")
    _land_ground(run_id, state, final)


def _drift(state: Dict, composed: Image.Image, final: Image.Image) -> List[str]:
    """Boxes the edit pass redrew into something else. Detection is per-box mean absolute
    difference — coarse on purpose: it may only find BROKEN (an object replaced), never
    judge blending, which is the pass's whole job."""
    if composed.size != final.size:
        final = final.resize(composed.size, Image.LANCZOS)
    a = np.asarray(composed, dtype=np.int16)
    b = np.asarray(final, dtype=np.int16)
    out = []
    for box in state["blockout"]["boxes"]:
        if box["kind"] not in ("building", "landmark", "decoration"):
            continue
        x0, y0 = box["x"] * CELL, box["y"] * CELL
        x1, y1 = x0 + box["w"] * CELL, y0 + box["h"] * CELL
        region_a, region_b = a[y0:y1, x0:x1], b[y0:y1, x0:x1]
        if region_a.size and np.abs(region_a - region_b).mean() > DRIFT_THRESHOLD:
            out.append(box["name"])
    return out


def _land_ground(run_id: str, state: Dict, img: Image.Image) -> None:
    root = _game_root(RunState(run_id).run_dir)
    img.save(root / state["ground_rel"])
    status = (db_store.game(run_id) or {}).get("status")
    if status == "built":
        stage_for_play(RunState(run_id).run_dir, run_id)
    logger.info("scene %s/%s: ground landed", run_id, state["scene_id"])


def _game_root(run_dir) -> Path:
    from maestro.codegen.staging import game_dir
    return game_dir(run_dir)


CONTINUATIONS = {"scene_mesh_from_image": _scene_mesh_from_image}
OPERATIONS = {"scene_mesh": _scene_mesh, "scene_terrain": _scene_terrain,
              "scene_embed": _scene_embed}
FINALIZERS = {"scene_objects": _finalize_scene_objects,
              "scene_embed": _finalize_scene_embed}
