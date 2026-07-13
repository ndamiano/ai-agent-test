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
from maestro.codegen.gates import game_files, game_dir

logger = logging.getLogger(__name__)

_PROMPTS = Path(__file__).resolve().parent / "prompts"
_DRAW_CALL = re.compile(r"\.(rect|circle|line|sprite|text)\s*\(")


def _content(resp) -> str:
    return ((resp.get("choices") or [{}])[0].get("message", {}) or {}).get("content", "") or ""


def _json_block(text: str) -> dict:
    m = re.search(r"```(?:json)?\s*\n(.*?)```", text, re.S)
    return json.loads(m.group(1) if m else text)


def _ts_block(text: str) -> str:
    from maestro.codegen.gates import extract_code
    return extract_code(text)


def _draws(src: str) -> bool:
    return bool(_DRAW_CALL.search(src))


def plan_assets(infer, spec: dict, files: dict) -> list:
    """LLM call 1: spec + game source → the sprite manifest [{id, prompt, w, h}]. The ids become the
    contract the draw rewrite keys on."""
    system = (_PROMPTS / "plan_assets.txt").read_text(encoding="utf-8")
    src = "\n\n".join(f"// ── {name} ──\n{code}" for name, code in files.items())
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


def reskin_file(infer, name: str, src: str, ids: list) -> str:
    """LLM call 2 (per drawing file): rewrite draw code to prefer kit.sprite(id) with shape fallback."""
    system = (_PROMPTS / "reskin_draw.txt").read_text(encoding="utf-8")
    user = (f"Available sprite ids: {', '.join(ids)}\n\nFile: {name}\n\n```ts\n{src}\n```\n\n"
            "Rewrite the file, skinning each drawn kind with its sprite id.")
    return _ts_block(infer(system, user, 6000))


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
    from tools.comfyui_tools import build_item_job, run_jobs, vram_bracket
    from tools.execution_context import execution_context

    assets_dir = game_dir(run_dir) / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)
    jobs = [build_item_job(s["prompt"]) for s in sprites]
    try:
        with execution_context(working_directory=str(assets_dir)), vram_bracket():
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


def add_assets(run_id: str, max_steps: int = 40) -> dict:
    """Skin a built run. Plan → reskin draw code → re-gate → render sprites → manifest → stage."""
    from maestro.codegen.run import run_build
    from maestro.codegen.tools import build_codegen_tools
    from maestro.codegen.gates import stage_for_play
    from llm_clients.connector_selector import get_connector
    from llm_clients.message_builder import MessageBuilder

    state = RunState.for_run(run_id)
    spec = state.read_spec()
    if spec is None:
        raise ValueError(f"no run {run_id!r}")
    conn = get_connector()

    def infer(system, user, mt):
        return _content(conn.generate_with_tools(
            MessageBuilder(system).add_user(user).build(), [], max_tokens=mt))

    files = game_files(state.run_dir)
    sprites = plan_assets(infer, spec, files)
    if not sprites:
        raise ValueError("asset plan produced no sprites")
    ids = [s["id"] for s in sprites]
    logger.info("assets %s: planned %d sprite(s): %s", run_id, len(sprites), ", ".join(ids))

    write = build_codegen_tools(state)["write_game_file"]
    for name, src in files.items():
        if not _draws(src):
            continue
        new = reskin_file(infer, name, src, ids)
        if new.strip() and new.strip() != src.strip():
            write(code=new, file=name)
            logger.info("assets %s: reskinned %s", run_id, name)

    result = run_build(run_id, max_steps=max_steps)   # re-gate; auto-fix any regression the reskin caused

    generated = generate_sprites(state.run_dir, sprites)
    write_manifest(state.run_dir, sprites)
    logger.info("assets %s: rendered %d/%d sprite(s)", run_id, len(generated), len(sprites))

    if result.ok:
        stage_for_play(state.run_dir, run_id)
    return {"ok": result.ok, "sprites": sprites, "generated": sorted(generated), "result": result}
