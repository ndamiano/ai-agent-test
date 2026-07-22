"""Assets stage — skin a built game's placeholder shapes with generated sprites.

  add_assets(run_id) → plan a sprite set from the frozen spec + game source, rewrite the drawing
  code to prefer each entity kind's sprite (falling back to the shape when the image is absent),
  re-gate, then render the sprites (ComfyUI) into game/assets/ + game/assets.json.

The skin is purely additive: `kit.sprite(id)` returns null headless and for any missing file, so a
reskinned game still passes every gate and still renders — just as shapes — with no images present.
The image render degrades soft: if ComfyUI is down, the draw rewrite + manifest still land and the
game plays as shapes; re-running with the server up fills in the pngs.
"""

import json
import logging
import re
from pathlib import Path

from maestro.state import RunState
from maestro.codegen.data_files import sprite_plan_from_data
from maestro.codegen.gates import game_files, game_dir

logger = logging.getLogger(__name__)

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
    from maestro.codegen.gates import extract_code
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
    from PIL import Image
    im = Image.open(path).convert("RGBA")
    bbox = im.split()[-1].getbbox()
    if not bbox:
        return
    pad = int(max(im.width, im.height) * pad_frac)
    box = (max(0, bbox[0] - pad), max(0, bbox[1] - pad),
           min(im.width, bbox[2] + pad), min(im.height, bbox[3] + pad))
    im.crop(box).save(path)


def generate_sprites(run_dir, sprites: list) -> set:
    """Render each sprite prompt to game/assets/<id>.png via ComfyUI. Soft-fails per sprite (and
    wholesale if the server is down) — returns the set of ids that produced a file."""
    from tools.comfyui_tools import build_item_job, run_jobs
    from tools.execution_context import execution_context

    assets_dir = game_dir(run_dir) / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)
    jobs = [build_item_job(s["prompt"]) for s in sprites]
    try:
        with execution_context(working_directory=str(assets_dir)):
            results = run_jobs(jobs)
    except Exception as e:
        logger.warning("sprite render skipped (ComfyUI unavailable?): %s", e)
        return set()

    done = set()
    for s, res in zip(sprites, results):
        saved = res.get("saved_paths") if isinstance(res, dict) else None
        if not saved:
            logger.info("sprite %s not rendered: %s", s["id"],
                        (res or {}).get("error", "no output"))
            continue
        dst = assets_dir / f"{s['id']}.png"
        Path(saved[0]).replace(dst)
        try:
            _autocrop(dst)
        except Exception as e:
            logger.warning("autocrop %s failed: %s", s["id"], e)
        done.add(s["id"])
    return done


def write_manifest(run_dir, sprites: list) -> None:
    manifest = {"sprites": [{"id": s["id"], "file": f"assets/{s['id']}.png",
                             "w": s["w"], "h": s["h"]} for s in sprites]}
    (game_dir(run_dir) / "assets.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8")


def generate_meshes(run_dir, meshes: list) -> set:
    """Render each mesh prompt to an image (ComfyUI) then turn each image into a textured GLB
    (TRELLIS) at game/assets/<id>.glb. Soft-fails wholesale if either backend is down (returns the
    empty set) — the mesh tags + manifest still land and the game renders its primitive shapes, so a
    re-run with the servers up fills the GLBs. Mirrors the 2D sprite soft-degrade."""
    import tempfile
    from tools.comfyui_tools import build_item_job, run_jobs, run_trellis_batch
    from tools.execution_context import execution_context

    assets_dir = game_dir(run_dir) / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        jobs = [build_item_job(m["prompt"]) for m in meshes]
        try:
            with execution_context(working_directory=tmp):
                results = run_jobs(jobs)
        except Exception as e:
            logger.warning("mesh image render skipped (ComfyUI unavailable?): %s", e)
            return set()
        for m, res in zip(meshes, results):
            saved = res.get("saved_paths") if isinstance(res, dict) else None
            if not saved:
                logger.info("mesh %s image not rendered: %s", m["id"],
                            (res or {}).get("error", "no output"))
                continue
            Path(saved[0]).replace(Path(tmp) / f"{m['id']}.png")   # TRELLIS keys the GLB on the stem
        try:
            return run_trellis_batch(tmp, str(assets_dir))
        except Exception as e:
            logger.warning("mesh render skipped (TRELLIS unavailable?): %s", e)
            return set()


def write_mesh_manifest(run_dir, meshes: list) -> None:
    manifest = {"meshes": [{"id": m["id"], "file": f"assets/{m['id']}.glb"} for m in meshes]}
    (game_dir(run_dir) / "assets.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8")


def add_assets(run_id: str, max_steps: int = 40) -> dict:
    """Skin a built run. Dispatches on the game's mode: 2D games get sprites (kit.sprite draw
    rewrite), 3D games get meshes (entity `mesh` tags → TRELLIS GLBs). Both are additive — the
    reskin + manifest always land and the game still renders as shapes if the image/mesh backend
    is down."""
    from llm_clients.connector_selector import get_connector
    from llm_clients.message_builder import MessageBuilder

    state = RunState.for_run(run_id)
    spec = state.read_spec()
    if spec is None:
        raise ValueError(f"no run {run_id!r}")
    conn = get_connector()

    def infer(system, user, mt):
        # reasoning="none" explicitly: these calls carry a small max_tokens, so a thinking model
        # spends the whole budget reasoning and returns an empty message.
        return _content(conn.generate_with_tools(
            MessageBuilder(system).add_user(user).build(), [], max_tokens=mt, reasoning="none"))

    files = game_files(state.run_dir)
    skin = _skin_3d if _is_3d(files) else _skin_2d
    return skin(run_id, state, spec, infer, files, max_steps)


def _reskin_and_gate(run_id, state, infer, files, ids, detect, reskin, max_steps) -> object:
    """Rewrite each matching file (detect → reskin), then re-gate and auto-fix any regression.
    Written straight to disk: the tool-side `write` is create-only (the fix loop's no-overwrite
    guarantee), and this deterministic stage replaces files by design — `name` comes from
    game_files(), so it is already an on-disk game filename."""
    from maestro.codegen.run import run_build
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


def _skin_2d(run_id, state, spec, infer, files, max_steps) -> dict:
    from maestro.codegen.gates import stage_for_play
    # The data rows' `look` prompts ARE the plan when a run has them — deterministic, no LLM call.
    sprites = sprite_plan_from_data(state.run_dir, "2d") or plan_assets(infer, spec, files)
    if not sprites:
        raise ValueError("asset plan produced no sprites")
    ids = [s["id"] for s in sprites]
    logger.info("assets %s: planned %d sprite(s): %s", run_id, len(sprites), ", ".join(ids))

    result = _reskin_and_gate(run_id, state, infer, files, ids, _draws, reskin_file, max_steps)

    generated = generate_sprites(state.run_dir, sprites)
    write_manifest(state.run_dir, sprites)
    logger.info("assets %s: rendered %d/%d sprite(s)", run_id, len(generated), len(sprites))

    if result.ok:
        stage_for_play(state.run_dir, run_id)
    return {"ok": result.ok, "mode": "2d", "sprites": sprites,
            "generated": sorted(generated), "result": result}


def _glb_dims(path: Path):
    """A GLB's model-space size [sx, sy, sz] from its POSITION accessors' min/max (the JSON chunk;
    node transforms ignored — TRELLIS emits a single untransformed mesh). None when unparseable."""
    import struct
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
    from maestro.codegen.worldgen_bridge import _write_world_ts

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


def _skin_3d(run_id, state, spec, infer, files, max_steps) -> dict:
    from maestro.codegen.gates import build_bundle, stage_for_play
    # Data-planned meshes still union the source's `mesh:` tags — a tagged entity MUST get a mesh.
    meshes = sprite_plan_from_data(state.run_dir, "3d")
    meshes = _add_required(meshes, _existing_mesh_ids(files)) if meshes \
        else plan_meshes(infer, spec, files)
    if not meshes:
        raise ValueError("mesh plan produced no meshes")
    ids = [m["id"] for m in meshes]
    logger.info("assets %s: planned %d mesh(es): %s", run_id, len(meshes), ", ".join(ids))

    result = _reskin_and_gate(run_id, state, infer, files, ids, _tags_shapes, reskin_mesh_file, max_steps)

    generated = generate_meshes(state.run_dir, meshes)
    write_mesh_manifest(state.run_dir, meshes)
    logger.info("assets %s: rendered %d/%d mesh(es)", run_id, len(generated), len(meshes))
    fitted = fit_building_boxes(state.run_dir)
    if fitted:
        logger.info("assets %s: fitted %d building box(es) to their meshes", run_id, fitted)
        build_bundle(state.run_dir)

    if result.ok:
        stage_for_play(state.run_dir, run_id)
    return {"ok": result.ok, "mode": "3d", "meshes": meshes,
            "generated": sorted(generated), "result": result}
