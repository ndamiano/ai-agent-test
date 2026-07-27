"""Assets stage — skin a built game's placeholder shapes with generated sprites.

  add_assets(run_id) → plan a sprite set from the frozen spec + game source, rewrite the drawing
  code to prefer each entity kind's sprite (falling back to the shape when the image is absent),
  re-gate, write game/assets.json, then ENQUEUE the renders and return. The images (and, in 3D,
  the meshes they feed) land on the queue; maestro.codegen.asset_chain finalizes the batch.

The skin is purely additive: `kit.sprite(id)` returns null headless and for any missing file, so a
reskinned game still passes every gate and still renders — just as shapes — with no images present.
So a render that never happens degrades soft: the draw rewrite + manifest already landed and the
game plays as shapes; re-running fills in the pngs.
"""

import base64
import json
import logging
import re
import struct
import threading
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Optional

from PIL import Image

from db import store as db_store
from llm_clients.connector import get_connector
from llm_clients.message_builder import MessageBuilder
from maestro.codegen import asset_chain
from maestro.codegen.data_files import sprite_plan_from_data
from maestro.codegen.gates import extract_code, game_dir, game_files
from maestro.codegen.run import BuildResult, run_build
from maestro.codegen.worldgen_bridge import _write_world_ts
from maestro.state import RunState
from tools.build_events import _emit
from tools.comfyui_tools import build_item_payload
from tools.execution_context import run_scope

logger = logging.getLogger(__name__)


class AlreadySkinning(Exception):
    """A skin (plan/wire/enqueue) is already in flight for this run."""


_skinning: set = set()
_skinning_lock = threading.Lock()


def is_skinning(run_id: str) -> bool:
    return run_id in _skinning


@contextmanager
def _exclusive(run_id: str):
    with _skinning_lock:
        if run_id in _skinning:
            raise AlreadySkinning(run_id)
        _skinning.add(run_id)
    try:
        yield
    finally:
        with _skinning_lock:
            _skinning.discard(run_id)


_PROMPTS = Path(__file__).resolve().parent / "prompts"
_DRAW_CALL = re.compile(r"\.(rect|circle|line|sprite|text)\s*\(")
_SHAPE_TAG = re.compile(r"""shape\s*:\s*["'](box|sphere)["']""")
_MODE_3D = re.compile(r"""mode\s*:\s*["']3d["']""")


_GENERATED = "// GENERATED"
def _content(resp) -> str:
    return ((resp.get("choices") or [{}])[0].get("message", {}) or {}).get("content", "") or ""


def _plannable_src(files: dict) -> str:
    """The source a planner reads: AUTHORED files only. A `// GENERATED` file (worldgen's world.ts is
    ~100KB of baked terrain heightfield + labelled parcels) is never rewritten (see _reskin_and_gate)
    and its objects are already `mesh:`-tagged, so its body is pure context bloat — feeding it blows the
    window. Its ids still reach the planner via _existing_mesh_ids, which scans the full file set."""
    return "\n\n".join(f"// ── {name} ──\n{code}" for name, code in files.items()
                       if not code.lstrip().startswith(_GENERATED))


def _json_block(text: str) -> dict:
    if not text.strip():
        raise ValueError("the planner returned no content — the model spent its whole token budget "
                         "on reasoning, or the inference call failed")
    m = re.search(r"```(?:json)?\s*\n(.*?)```", text, re.S)
    return json.loads(m.group(1) if m else text)


def _ts_block(text: str) -> str:
    return extract_code(text)


def _draws(src: str) -> bool:
    return bool(_DRAW_CALL.search(src))


def _tags_shapes(src: str) -> bool:
    return bool(_SHAPE_TAG.search(src))


def _is_3d(files: dict) -> bool:
    return any(_MODE_3D.search(src) for src in files.values())


def plan_assets(infer, spec: dict, files: dict) -> list:
    """LLM call 1: spec + game source → the sprite manifest [{id, prompt, w, h}]. The ids become the
    contract the draw rewrite keys on."""
    system = (_PROMPTS / "plan_assets.txt").read_text(encoding="utf-8")
    src = _plannable_src(files)
    user = (f"SPEC:\n{json.dumps(spec.get('design', spec), ensure_ascii=False, indent=2)}\n\n"
            f"GAME SOURCE:\n{src}\n\nList the sprites.")
    plan = _json_block(infer(system, user, 2000))
    sprites = plan.get("sprites", []) if isinstance(plan, dict) else []
    out, seen = [], set()
    for s in sprites:
        sid = str(s.get("id", "")).strip().lower()
        if not sid or sid in seen or not s.get("prompt"):
            continue
        seen.add(sid)
        out.append({"id": sid, "prompt": str(s["prompt"]),
                    "w": int(s.get("w", 32)), "h": int(s.get("h", 32))})
    return out


def _rewrite_budget(src: str) -> int:
    """Output budget for a whole-file rewrite: the completion is the whole file back, so a flat cap
    truncates a big one mid-token and the syntax error lands on disk. ~3 chars/token plus headroom."""
    return max(6000, len(src) // 2)


def _looks_truncated(src: str, new: str) -> bool:
    """A rewrite that lost a big fraction of the body, or ends mid-block, is a cut-off completion."""
    return len(new) < len(src) * 0.6 or new.count("{") != new.count("}")


def reskin_file(infer, name: str, src: str, ids: list) -> str:
    """LLM call 2 (per drawing file): rewrite draw code to prefer kit.sprite(id) with shape fallback."""
    system = (_PROMPTS / "reskin_draw.txt").read_text(encoding="utf-8")
    user = (f"Available sprite ids: {', '.join(ids)}\n\nFile: {name}\n\n```ts\n{src}\n```\n\n"
            "Rewrite the file, skinning each drawn kind with its sprite id.")
    return _ts_block(infer(system, user, _rewrite_budget(src)))


# matches both a code tag `mesh: "id"` and a JSON field `"mesh":"id"` (worldgen bakes ids into WORLD)
_MESH_TAG = re.compile(r"""["']?mesh["']?\s*:\s*["']([A-Za-z0-9_]+)["']""")


def _existing_mesh_ids(files: dict) -> list:
    """Mesh ids ALREADY tagged in the source (e.g. worldgen buildings tagged `mesh:"timber_house"`).
    These are a hard contract — every one MUST get a generated mesh, or that entity renders as a bare
    box. Returned lowercased + de-duped, in first-seen order."""
    seen, out = set(), []
    for src in files.values():
        for m in _MESH_TAG.finditer(src):
            mid = m.group(1).lower()
            if mid not in seen:
                seen.add(mid)
                out.append(mid)
    return out


def plan_meshes(infer, spec: dict, files: dict) -> list:
    """LLM call 1 (3D): spec + source → the mesh manifest [{id, prompt, w, h, d}]. Each entry is one
    on-screen object KIND; `prompt` is an IMAGE prompt (TRELLIS turns a rendered image into the mesh),
    the id becomes the tag the entities key on. Any `mesh:"id"` tag already in the source (a generated
    world's labelled buildings/props) is a REQUIRED id — we pass them in and union them back so none
    is silently dropped, so every tagged entity gets a real mesh instead of a placeholder box."""
    required = _existing_mesh_ids(files)
    system = (_PROMPTS / "plan_meshes.txt").read_text(encoding="utf-8")
    src = _plannable_src(files)
    req_note = (f"\n\nREQUIRED ids (entities in the source already carry these `mesh` tags — you MUST "
                f"output a mesh for EACH, with a vivid prompt + dims): {', '.join(required)}"
                if required else "")
    user = (f"SPEC:\n{json.dumps(spec.get('design', spec), ensure_ascii=False, indent=2)}\n\n"
            f"GAME SOURCE:\n{src}{req_note}\n\nList the meshes.")
    plan = _json_block(infer(system, user, 3000))
    meshes = plan.get("meshes", []) if isinstance(plan, dict) else []
    out, seen = [], set()
    for m in meshes:
        mid = str(m.get("id", "")).strip().lower()
        if not mid or mid in seen or not m.get("prompt"):
            continue
        seen.add(mid)
        out.append({"id": mid, "prompt": str(m["prompt"]),
                    "w": int(m.get("w", 1)), "h": int(m.get("h", 1)), "d": int(m.get("d", 1))})
    return _add_required(out, required)


def _add_required(meshes: list, required: list) -> list:
    """Safety net: a required id the plan dropped still gets a mesh (prompt derived from the id)."""
    seen = {m["id"] for m in meshes}
    for mid in required:
        if mid not in seen:
            label = mid.replace("_", " ")
            meshes.append({"id": mid, "prompt": f"a single {label}, medieval village style, one clean "
                           f"3/4 view of the whole object, centered on a plain neutral background",
                           "w": 4, "h": 4, "d": 4})
    return meshes


def reskin_mesh_file(infer, name: str, src: str, ids: list) -> str:
    """LLM call 2 (3D, per file that builds entities): tag each entity of a listed kind with
    `mesh: "<id>"` next to its shape. The runtime swaps the primitive for the GLB when present."""
    system = (_PROMPTS / "reskin_mesh.txt").read_text(encoding="utf-8")
    user = (f"Available mesh ids: {', '.join(ids)}\n\nFile: {name}\n\n```ts\n{src}\n```\n\n"
            "Rewrite the file, tagging each entity with its mesh id.")
    return _ts_block(infer(system, user, _rewrite_budget(src)))


def _autocrop(path: Path, pad_frac: float = 0.06) -> None:
    """Tighten a matted sprite to its opaque subject. ComfyUI renders on a 1024 frame with wide
    transparent margins, so a sprite drawn at the entity's box size shows the subject at a fraction of
    the box — cropping to the alpha bbox (plus a small margin) makes it fill the box like a real sprite."""
    im = Image.open(path).convert("RGBA")
    bbox = im.split()[-1].getbbox()
    if not bbox:
        return
    pad = int(max(im.width, im.height) * pad_frac)
    box = (max(0, bbox[0] - pad), max(0, bbox[1] - pad),
           min(im.width, bbox[2] + pad), min(im.height, bbox[3] + pad))
    im.crop(box).save(path)


def start_asset_chain(run_id: str, plan: list, mode: str, gate_ok: bool,
                      build_id: Optional[str] = None) -> Optional[str]:
    """Enqueue every asset job at once and return the batch id. NOTHING WAITS: each job carries
    what follows it (in 3D, the TRELLIS job its image feeds) and the batch's finalize, so the
    whole stage lives in the queue where its depth is real work the scaler can act on and no
    worker idles out with the next job seconds away.

    None when nothing was enqueued — an empty plan, every prompt blocked, or a budget refusal on
    the first job. The caller finalizes directly, since no completion ever will."""
    batch_id = uuid.uuid4().hex[:16]
    then = {"enqueue": "mesh_from_image", "finalize": "skin"} if mode == "3d" \
        else {"operations": ["save_sprite"], "finalize": "skin"}
    enqueued = 0
    for item in plan:
        payload = build_item_payload(item["prompt"])
        if payload is None:
            logger.warning("assets %s: %s blocked by the safety filter — not sent",
                           run_id, item["id"])
            continue
        try:
            db_store.enqueue_job("image", payload, game_id=run_id, build_id=build_id,
                                 batch_id=batch_id,
                                 metadata={"run_id": run_id, "asset_id": item["id"], "mode": mode,
                                           "gate_ok": gate_ok, "then": then})
        except db_store.InsufficientCompute as e:
            logger.error("assets %s: budget refused after %d job(s): %s", run_id, enqueued, e)
            break
        enqueued += 1
    logger.info("assets %s: enqueued %d/%d asset job(s) as batch %s",
                run_id, enqueued, len(plan), batch_id)
    return batch_id if enqueued else None


def _asset_context(run_dir, asset_id: str, mode: str) -> Optional[str]:
    """The asset's ORIGINAL styled prompt — assets.json first (manifests persist it), the data
    rows' `look` as the fallback for games skinned before prompts were persisted."""
    try:
        manifest = json.loads((game_dir(run_dir) / "assets.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        manifest = {}
    for entry in (manifest.get("sprites") or []) + (manifest.get("meshes") or []):
        if entry.get("id") == asset_id and entry.get("prompt"):
            return entry["prompt"]
    for item in sprite_plan_from_data(run_dir, mode) or []:
        if item["id"] == asset_id:
            return item["prompt"]
    return None


def _merge_regen_prompt(infer, original: Optional[str], note: str) -> str:
    """Fold the user's change note into the asset's original prompt — "make it redder" is an
    instruction, not an image prompt, and sending it wholesale loses the asset entirely. No
    original context (or a dead llm queue) degrades to the note verbatim: a regen must never
    fail on its helper."""
    if not original:
        return note
    system = (_PROMPTS / "regen_asset.txt").read_text(encoding="utf-8")
    user = f"ORIGINAL PROMPT:\n{original}\n\nCHANGE REQUEST:\n{note}\n\nOutput the new prompt."
    try:
        merged = infer(system, user, 400).strip().strip('"`')
    except Exception as e:
        logger.warning("regenerate: prompt merge failed (%s) — using the note verbatim", e)
        return note
    return merged or note


def _regen_init_image(run_dir, asset_id: str, mode: str) -> Optional[str]:
    """The img2img init as base64: the sprite itself in 2D; in 3D the mesh's SOURCE render
    (<id>.src.png, saved by the chain) — a GLB can't seed an image model. None → the caller
    falls back to a full render."""
    name = f"{asset_id}.src.png" if mode == "3d" else f"{asset_id}.png"
    path = game_dir(run_dir) / "assets" / name
    if not path.exists():
        return None
    return base64.b64encode(path.read_bytes()).decode("ascii")


def regenerate_asset(run_id: str, asset_id: str, prompt: str, mode: str = "full",
                     build_id: Optional[str] = None) -> Optional[str]:
    """Re-render ONE asset of a built+skinned game — without re-skinning the whole game. The
    source already references the asset id (assets.json + the draw/tag it keys on), so this is a
    pure file swap: a one-job batch that saves the new png/glb and RE-STAGES it into
    runtime/games/<run_id>/ through the SAME `skin` finalize a full re-skin uses (stage_for_play +
    assets_done), so the played game and the cockpit gallery both pick it up and the existing
    assets_done refetch just works. Returns the batch id, or None if the prompt was blocked by the
    safety filter.

    The user's text is a CHANGE NOTE, not the finished prompt: it is merged with the asset's saved
    original prompt (one small llm call) so "give him a red cape" keeps the goblin. mode="img2img"
    additionally seeds the render from the existing image (partial denoise), preserving the
    composition; it degrades to a full render when no init image exists.

    gate_ok is True unconditionally: the game already passed its gates and is staged, so the
    finalize must re-stage the swapped file (stage_for_play only runs when gate_ok)."""
    state = RunState(run_id)
    render_mode = "3d" if _is_3d(game_files(state.run_dir)) else "2d"
    with run_scope(run_id):
        original = _asset_context(state.run_dir, asset_id, render_mode)
        final = _merge_regen_prompt(_make_infer(), original, prompt)
        init_b64 = _regen_init_image(state.run_dir, asset_id, render_mode) \
            if mode == "img2img" else None
        if mode == "img2img" and init_b64 is None:
            logger.info("regenerate %s/%s: no init image — falling back to a full render",
                        run_id, asset_id)
        payload = build_item_payload(final, init_image_b64=init_b64)
        if payload is None:
            logger.warning("regenerate %s/%s: prompt blocked by the safety filter", run_id, asset_id)
            return None
        then = {"enqueue": "mesh_from_image", "finalize": "skin"} if render_mode == "3d" \
            else {"operations": ["save_sprite"], "finalize": "skin"}
        batch_id = uuid.uuid4().hex[:16]
        db_store.enqueue_job("image", payload, game_id=run_id, build_id=build_id,
                             batch_id=batch_id,
                             metadata={"run_id": run_id, "asset_id": asset_id, "mode": render_mode,
                                       "gate_ok": True, "then": then})
    logger.info("regenerate %s: enqueued asset %s (%s, %s) as batch %s",
                run_id, asset_id, render_mode, mode, batch_id)
    return batch_id


def write_manifest(run_dir, sprites: list) -> None:
    # `prompt` rides along so a later regenerate knows what the asset IS, not just its file.
    manifest = {"sprites": [{"id": s["id"], "file": f"assets/{s['id']}.png",
                             "w": s["w"], "h": s["h"], "prompt": s["prompt"]} for s in sprites]}
    (game_dir(run_dir) / "assets.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8")


def write_mesh_manifest(run_dir, meshes: list) -> None:
    manifest = {"meshes": [{"id": m["id"], "file": f"assets/{m['id']}.glb",
                            "prompt": m["prompt"]} for m in meshes]}
    (game_dir(run_dir) / "assets.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8")


def add_assets(run_id: str, max_steps: int = 40, build_id: Optional[str] = None) -> dict:
    """Skin a built run. Dispatches on the game's mode: 2D games get sprites (kit.sprite draw
    rewrite), 3D games get meshes (entity `mesh` tags → TRELLIS GLBs). Both are additive — the
    reskin + manifest always land and the game still renders as shapes if the image/mesh backend
    is down.

    Returns once the asset jobs are ENQUEUED, not once they are rendered: the batch finishes on
    the queue and its finalize emits assets_done. `batch_id` in the return is what to watch."""
    state = RunState(run_id)
    spec = state.read_spec()
    if spec is None:
        raise ValueError(f"no run {run_id!r}")

    with _exclusive(run_id):
        files = game_files(state.run_dir)
        skin = _skin_3d if _is_3d(files) else _skin_2d
        # The image and mesh queues are the most expensive GPU work the platform runs. Without the
        # scope their jobs enqueue with no game_id, so nothing is metered and nothing is gated —
        # this stage ran entirely off the books.
        with run_scope(run_id):
            return skin(run_id, state, spec, _make_infer(), files, max_steps, build_id)


def _make_infer():
    conn = get_connector()

    def infer(system, user, mt):
        # reasoning="none" explicitly: these calls carry a small max_tokens, so a thinking model
        # spends the whole budget reasoning and returns an empty message.
        return _content(conn.generate_with_tools(
            MessageBuilder(system).add_user(user).build(), [], max_tokens=mt, reasoning="none"))

    return infer


def _regate(run_id, state, max_steps) -> object:
    """Confirm the game is still green — the path where kit.spawn already bound every asset id.

    NOTHING was rewritten here, so there is nothing for a build to fix: a full run_build re-ran the
    entire chain, spec audit included, to answer a question the gates answer on their own. Measured:
    two asset runs on one game spent a 7-claim audit each (~50 llm turns) to enqueue 8 images that
    needed no model call at all. A gate SWEEP is the honest check — deterministic, zero llm, and it
    still catches a game that was never green to begin with. (max_steps is unused: a sweep does not
    step.)"""
    from maestro.codegen.build_chain import collect_errors   # noqa: PLC0415 — build_chain imports us
    from maestro.codegen.module import CodegenModule          # noqa: PLC0415
    from maestro.modules.context import build_context         # noqa: PLC0415

    logger.info("assets %s: data-bound — no reskin rewrite needed", run_id)
    errors = [e for _, e in collect_errors(CodegenModule(), build_context(state.read_spec(), state))]
    if errors:
        logger.warning("assets %s: game is not green (%d unmet): %s", run_id, len(errors),
                       ", ".join(e.code for e in errors))
    return BuildResult(not errors, 0, 0.0, errors)


def _reskin_and_gate(run_id, state, infer, files, ids, detect, reskin, max_steps) -> object:
    """Rewrite each matching file (detect → reskin), then re-gate and auto-fix any regression.
    Written straight to disk: the tool-side `write` is create-only (the fix loop's no-overwrite
    guarantee), and this deterministic stage replaces files by design — `name` comes from
    game_files(), so it is already an on-disk game filename."""
    for name, src in files.items():
        if not detect(src):
            continue
        if src.lstrip().startswith(_GENERATED):
            continue   # a generated file (e.g. worldgen's world.ts) is already tagged — never rewrite it
        new = reskin(infer, name, src, ids)
        if new.strip() and _looks_truncated(src, new):
            logger.warning("assets %s: reskin of %s came back truncated (%d chars from %d) — file "
                           "left untouched", run_id, name, len(new), len(src))
        elif new.strip() and new.strip() != src.strip():
            (game_dir(state.run_dir) / name).write_text(new, encoding="utf-8")
            logger.info("assets %s: reskinned %s", run_id, name)
        else:
            logger.warning("assets %s: reskin of %s produced %s — file left untouched (assets will "
                           "not be wired here)", run_id, name, "nothing" if not new.strip() else "no change")
    return run_build(run_id, max_steps=max_steps)


def _skin_2d(run_id, state, spec, infer, files, max_steps, build_id=None) -> dict:
    # The data rows' `look` prompts ARE the plan when a run has them — deterministic, no LLM call.
    from_data = sprite_plan_from_data(state.run_dir, "2d")
    sprites = from_data or plan_assets(infer, spec, files)
    if not sprites:
        raise ValueError("asset plan produced no sprites")
    ids = [s["id"] for s in sprites]
    logger.info("assets %s: planned %d sprite(s): %s", run_id, len(sprites), ", ".join(ids))

    # Skip the LLM rewrite only when the game ALREADY binds its assets through the kit; a hand-drawn
    # draw() still needs wiring or the sprites we just planned would never appear.
    # A data plan needs no rewrite: kit.spawn binds a row-typed entity's asset id itself. Whether
    # the game actually spawns the things the art depicts is NOT tested — that was a static guess at
    # a runtime fact, it read a variable `type` as "unbound", and it cost a build 140 steps down a
    # rewrite path it never needed. An unbound sprite is a thing a human sees and asks for.
    result = _regate(run_id, state, max_steps) if from_data \
        else _reskin_and_gate(run_id, state, infer, files, ids, _draws, reskin_file, max_steps)

    # The manifest is a pure function of the plan, so it lands now rather than at finalize — a
    # missing sprite still renders as its shape, which is the stage's soft-degrade either way.
    write_manifest(state.run_dir, sprites)
    batch_id = _launch(run_id, sprites, "2d", result.ok, build_id)
    return {"ok": result.ok, "mode": "2d", "sprites": sprites,
            "batch_id": batch_id, "result": result}


def _glb_dims(path: Path):
    """A GLB's model-space size [sx, sy, sz] from its POSITION accessors' min/max (the JSON chunk;
    node transforms ignored — TRELLIS emits a single untransformed mesh). None when unparseable."""
    try:
        raw = path.read_bytes()
        if raw[:4] != b"glTF":
            return None
        ln = struct.unpack_from("<I", raw, 12)[0]
        doc = json.loads(raw[20:20 + ln])
        lo = [float("inf")] * 3
        hi = [float("-inf")] * 3
        for mesh in doc.get("meshes", []):
            for prim in mesh.get("primitives", []):
                idx = (prim.get("attributes") or {}).get("POSITION")
                if idx is None:
                    continue
                acc = doc["accessors"][idx]
                for k in range(3):
                    lo[k] = min(lo[k], acc["min"][k])
                    hi[k] = max(hi[k], acc["max"][k])
        dims = [hi[k] - lo[k] for k in range(3)]
        return dims if all(d > 1e-6 for d in dims) else None
    except Exception:
        return None


def fit_building_boxes(run_dir) -> int:
    """Shrink each skinned building's box to the dims its GLB actually renders at. The renderer
    scales a model UNIFORMLY to fit inside the entity box, so a slender stall in a fat parcel leaves
    invisible collision air around it. The parcel stays the SLOT (position unchanged); the box —
    which is both the render bound and the avoidRects hitbox — becomes the fitted mesh size, so
    walls sit exactly where the model shows them. No-mesh buildings keep the parcel box (the
    primitive slab fills it exactly). Returns how many buildings were fitted."""
    world_path = game_dir(run_dir) / "world.ts"
    if not world_path.exists():
        return 0
    src = world_path.read_text(encoding="utf-8")
    m = re.search(r"export const WORLD: any = (\{.*?\});\n", src, re.S)
    if not m:
        return 0
    data = json.loads(m.group(1))
    assets = game_dir(run_dir) / "assets"
    fitted = 0
    for b in data.get("buildings", []):
        dims = _glb_dims(assets / f"{b.get('mesh', '')}.glb") if b.get("mesh") else None
        if not dims:
            continue
        sx, sy, sz = dims
        u = min(b["w"] / sx, b["h"] / sy, b["d"] / sz)
        b["w"], b["h"], b["d"] = round(sx * u, 2), round(sy * u, 2), round(sz * u, 2)
        fitted += 1
    if fitted:
        _write_world_ts(world_path, data)
    return fitted


def _skin_3d(run_id, state, spec, infer, files, max_steps, build_id=None) -> dict:
    # Data-planned meshes still union the source's `mesh:` tags — a tagged entity MUST get a mesh.
    from_data = sprite_plan_from_data(state.run_dir, "3d")
    meshes = _add_required(from_data, _existing_mesh_ids(files)) if from_data \
        else plan_meshes(infer, spec, files)
    if not meshes:
        raise ValueError("mesh plan produced no meshes")
    ids = [m["id"] for m in meshes]
    logger.info("assets %s: planned %d mesh(es): %s", run_id, len(meshes), ", ".join(ids))

    # Skip the tagging LLM call only when the game already spawns the planned ids (kit.spawn binds
    # `mesh: <row id>`); a hand-spawned entity still needs the model to tag it.
    result = _regate(run_id, state, max_steps) if from_data \
        else _reskin_and_gate(run_id, state, infer, files, ids, _tags_shapes, reskin_mesh_file, max_steps)

    write_mesh_manifest(state.run_dir, meshes)
    batch_id = _launch(run_id, meshes, "3d", result.ok, build_id)
    return {"ok": result.ok, "mode": "3d", "meshes": meshes,
            "batch_id": batch_id, "result": result}


def _launch(run_id: str, plan: list, mode: str, gate_ok: bool,
            build_id: Optional[str]) -> Optional[str]:
    """Start the chain, or finalize immediately when it enqueued nothing — with no jobs in the
    batch no completion will ever fire, and the stage would hang unreported."""
    batch_id = start_asset_chain(run_id, plan, mode, gate_ok, build_id)
    if batch_id is None:
        asset_chain.finalize_now({"run_id": run_id, "mode": mode, "gate_ok": gate_ok,
                                  "then": {"finalize": "skin"}}, build_id)
    return batch_id


def start_assets_early(run_id: str, run_dir, spec: dict) -> Optional[str]:
    """The EARLY asset lane: a data game's render plan is its rows' `look` prompts, which exist the
    moment the data lands — so the GPU renders sprites/meshes DURING the build instead of after a
    click. Renders are keyed to row ids, so art rendered before the source binds it is never
    wasted; WIRING (and staging) is the green lane's job (auto_skin / the build finalize).

    Returns the batch id; "" when the lane is settled with nothing to watch (assets already exist,
    a batch is already live, or the plan enqueued nothing); None when there is no plan YET — the
    rows may still gain looks, so the caller retries next sweep."""
    if (game_dir(run_dir) / "assets.json").exists() or db_store.has_active_batch(run_id):
        return ""
    mode = "3d" if spec.get("mode") == "3d" else "2d"
    plan = sprite_plan_from_data(run_dir, mode)
    if not plan:
        return None
    if mode == "3d":
        plan = _add_required(plan, _existing_mesh_ids(game_files(run_dir)))
        write_mesh_manifest(run_dir, plan)
    else:
        write_manifest(run_dir, plan)
    build_id = db_store.create_build(run_id, kind="assets")
    db_store.build_started(build_id)
    batch_id = start_asset_chain(run_id, plan, mode, gate_ok=False, build_id=build_id)
    if batch_id is None:
        db_store.build_finished(build_id, "failed")
        return ""
    _emit("assets_started", run_id)
    logger.info("assets %s: early lane enqueued %d render(s) as batch %s",
                run_id, len(plan), batch_id)
    return batch_id


def auto_skin(run_id: str, early_batch: Optional[str] = None, max_steps: int = 40) -> None:
    """The GREEN lane: a build that finalizes ok finishes the asset story without a click.

    No early batch → the classic full skin (plan → wire → render), exactly what the Skin button
    did. Early batch → the renders exist or are in flight, so all that can be missing is WIRING
    (a game that spawns none of the planned ids, so nothing carries them) and, in 3D,
    mesh ids tagged during authoring that the data-time plan couldn't see — top up just those."""
    if is_skinning(run_id):
        return   # an in-flight skin owns the endgame; its own re-gate build re-enters here
    state = RunState(run_id)
    run_dir = state.run_dir
    if not early_batch:
        if (game_dir(run_dir) / "assets.json").exists() or db_store.has_active_batch(run_id):
            return
        build_id = db_store.create_build(run_id, kind="assets")
        db_store.build_started(build_id)
        _emit("assets_started", run_id)
        try:
            add_assets(run_id, build_id=build_id)
        except AlreadySkinning:
            db_store.build_finished(build_id, "failed")
        except Exception:
            logger.exception("auto-skin failed for %s", run_id)
            _emit("assets_done", run_id, ok=False, mode=None, rendered=[])
            db_store.build_finished(build_id, "failed")
        return

    with _exclusive(run_id):
        files = game_files(run_dir)
        mode3d = _is_3d(files)
        from_data = sprite_plan_from_data(run_dir, "3d" if mode3d else "2d") or []
        plan = _add_required(list(from_data), _existing_mesh_ids(files)) if mode3d else from_data
        if mode3d:
            done = {j["metadata"].get("asset_id") for j in db_store.batch_jobs(early_batch)}
            missing = [m for m in plan if m["id"] not in done]
            if missing:
                logger.info("assets %s: topping up %d mesh(es) the early plan couldn't see: %s",
                            run_id, len(missing), ", ".join(m["id"] for m in missing))
                write_mesh_manifest(run_dir, plan)
                build_id = db_store.create_build(run_id, kind="assets")
                db_store.build_started(build_id)
                if start_asset_chain(run_id, missing, "3d", gate_ok=True,
                                     build_id=build_id) is None:
                    db_store.build_finished(build_id, "failed")
        ids = [p["id"] for p in plan]
        if not ids:
            return
        if from_data:
            return   # a data plan binds through kit.spawn — the early renders wire themselves
        infer = _make_infer()
        with run_scope(run_id):
            if mode3d:
                _reskin_and_gate(run_id, state, infer, files, ids, _tags_shapes,
                                 reskin_mesh_file, max_steps)
            else:
                _reskin_and_gate(run_id, state, infer, files, ids, _draws,
                                 reskin_file, max_steps)
