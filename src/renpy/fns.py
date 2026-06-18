import os
import shutil
from pathlib import Path
from typing import Dict, List

from renpy._script import (
    _postprocess_script,
    _find_script_issues,
    _stitch_script,
    _write_options_rpy,
    run_final_lint,
)
from renpy.renpy_builder import _copy_templates, _distribute
from utils.image import write_solid_png


def _get_sdk_path() -> str:
    try:
        from config.settings_manager import settings_manager
        sdk = settings_manager.get_settings().get("renpy_sdk_path") or ""
        if sdk:
            return sdk
    except Exception:
        pass
    return os.environ.get("RENPY_SDK", "")


def generate_images(inputs: Dict, working_dir: Path) -> Dict:
    from tools.comfyui_tools import (
        build_character_job, build_background_job,
        build_cg_job, build_title_card_job,
        generate_images_batch,
    )

    premise  = inputs.get("premise", {})
    manifest = _merge_cast_into_manifest(premise, inputs.get("asset_manifest", {}))

    images_dir = working_dir / "game_output" / "game" / "images"
    images_dir.mkdir(parents=True, exist_ok=True)

    premise_chars = {c["id"]: c for c in premise.get("characters", [])}
    job_meta: List[Dict] = []
    jobs:     List[Dict] = []

    for bg in manifest.get("backgrounds", []):
        bg_file = bg["image_file"]
        job_meta.append({"file": bg_file, "dest": images_dir / bg_file, "kind": "bg"})
        jobs.append(build_background_job(bg.get("description", bg.get("name", bg["id"]))))

    for char in manifest.get("characters", []):
        img_file = char.get("image_file", f"{char['id']}.png")
        job_meta.append({"file": img_file, "dest": images_dir / img_file, "kind": "char"})
        jobs.append(build_character_job({**premise_chars.get(char["id"], {}), **char}))

    for cg in manifest.get("cgs", []):
        img_file = cg.get("image_file", f"{cg['id']}.png")
        job_meta.append({"file": img_file, "dest": images_dir / img_file, "kind": "cg"})
        jobs.append(build_cg_job(cg.get("description", cg["id"])))

    title_card = manifest.get("title_card", {})
    if title_card.get("description"):
        tc_file = title_card.get("image_file", "title_card.png")
        job_meta.append({"file": tc_file, "dest": images_dir / tc_file, "kind": "title_card"})
        jobs.append(build_title_card_job(title_card["description"]))

    print(f"    [images]  generating {len(jobs)} image(s)")
    results   = generate_images_batch(jobs)
    generated: List[str] = []
    failed:    List[Dict] = []

    for meta, result in zip(job_meta, results):
        filepath = meta["dest"]
        img_file = meta["file"]
        if result.get("success") and result.get("saved_paths"):
            shutil.copy2(result["saved_paths"][0], filepath)
            generated.append(img_file)
            print(f"    [images]  ok: {img_file}")
        else:
            error = result.get("error", "unknown")
            print(f"    [images]  failed ({error}), placeholder: {img_file}")
            kind = meta["kind"]
            if kind == "char":
                w, h, color = 512, 768, (92, 58, 92)
            elif kind in ("cg", "title_card"):
                w, h, color = 1280, 720, (40, 20, 60) if kind == "cg" else (20, 30, 60)
            else:
                w, h, color = 1280, 720, (58, 58, 92)
            write_solid_png(filepath, w, h, color)
            failed.append({"file": img_file, "error": error})

    return {"status": "ok", "generated": generated, "failed": failed}


def _merge_cast_into_manifest(premise: Dict, manifest: Dict) -> Dict:
    """premise.characters is the single source of truth for the cast.

    Sprite defines, placeholder pngs, image generation and the lint's valid-speaker set
    all key off asset_manifest.characters — but the cast's identity lives in premise. If
    the agent leaves asset_manifest.characters incomplete, every premise speaker gets
    flagged "not defined" even though Ren'Py defines it from premise. So backfill a
    manifest entry for every premise character (existing manifest entries win as image
    overrides). The agent never has to duplicate the cast into the manifest.
    """
    chars = list(manifest.get("characters", []))
    have = {c.get("id") for c in chars if isinstance(c, dict)}
    for pc in premise.get("characters", []):
        cid = pc.get("id")
        if cid and cid not in have:
            chars.append({"id": cid, "image_file": f"{cid}.png",
                          "description": pc.get("description") or pc.get("voice") or cid})
    return {**manifest, "characters": chars}


def _ensure_placeholder_images(manifest: Dict, game_dir: str) -> None:
    """Write a solid-color placeholder for every declared image that isn't on disk.

    A missing asset must not block the build — Ren'Py lint rejects unloadable images.
    generate_asset (comfyui) upgrades these to real art later; until then the game
    still builds and runs. Filenames mirror _stitch_script's defaults exactly.
    """
    images_dir = os.path.join(game_dir, "images")
    os.makedirs(images_dir, exist_ok=True)

    def _put(filename: str, w: int, h: int, color):
        if not filename:
            return
        path = os.path.join(images_dir, filename)
        if not os.path.exists(path):
            write_solid_png(Path(path), w, h, color)

    for bg in manifest.get("backgrounds", []):
        bg_id = bg.get("id", "")
        default = (bg_id[3:] + ".png") if bg_id.startswith("bg_") else f"{bg_id}.png"
        _put(bg.get("image_file", default), 1280, 720, (58, 58, 92))
    for ch in manifest.get("characters", []):
        _put(ch.get("image_file", f"{ch.get('id', '')}.png"), 512, 768, (92, 58, 92))
    for cg in manifest.get("cgs", []):
        _put(cg.get("image_file", f"{cg.get('id', '')}.png"), 1280, 720, (40, 20, 60))
    tc = manifest.get("title_card", {})
    _put(tc.get("image_file", ""), 1280, 720, (20, 30, 60))


def build(inputs: Dict, working_dir: Path, distribute: bool = True) -> Dict:
    brief        = inputs.get("brief", {})
    premise      = inputs.get("premise", {})
    manifest     = _merge_cast_into_manifest(premise, inputs.get("asset_manifest", {}))
    node_scripts = inputs.get("node_scripts", {})
    scripts      = node_scripts.get("scripts", {})
    node_ids     = node_scripts.get("node_ids", list(scripts.keys()))

    title      = brief.get("title", "Untitled")
    output_dir = str(working_dir / "game_output")

    valid_backgrounds = {bg["id"] for bg in manifest.get("backgrounds", [])}
    valid_characters  = {c["id"] for c in manifest.get("characters", [])} | {"act"}
    valid_cgs         = {cg["id"] for cg in manifest.get("cgs", [])}
    valid_labels      = set(node_ids) | {"start", "splashscreen", "main_menu"}

    # Do NOT rewrite the agent's content. Surface structural issues (e.g. a jump to a
    # node that doesn't exist) so the agent builds the missing piece instead of the
    # build mutilating what it wrote.
    script_issues = {
        nid: issue
        for nid in node_ids
        if scripts.get(nid)
        and (issue := _find_script_issues(scripts[nid], valid_labels,
                                          valid_backgrounds, valid_characters, valid_cgs))
    }
    if script_issues:
        return {"status": "built", "output_dir": output_dir,
                "script_issues": script_issues, "lint": {"error_count": None}}

    for nid in node_ids:
        if scripts.get(nid):
            scripts[nid] = _postprocess_script(scripts[nid], valid_characters)

    full_script = _stitch_script(premise, manifest, scripts, node_ids)

    game_dir = os.path.join(output_dir, "game")
    os.makedirs(game_dir, exist_ok=True)

    _write_options_rpy(game_dir, title)

    script_path = os.path.join(game_dir, "script.rpy")
    with open(script_path, "w", encoding="utf-8") as f:
        f.write(full_script)
    sdk_path = _get_sdk_path()
    _copy_templates(game_dir, sdk_path)

    # A declared-but-missing image must not fail the build; placeholder it (real art
    # comes from generate_asset). This keeps the compile gate about the script, not assets.
    _ensure_placeholder_images(manifest, game_dir)

    # Overwrite placeholder main menu background with generated title card
    title_card_src = os.path.join(game_dir, "images", "title_card.png")
    gui_main_menu  = os.path.join(game_dir, "gui", "main_menu.png")
    if os.path.exists(title_card_src) and os.path.exists(os.path.dirname(gui_main_menu)):
        shutil.copy2(title_card_src, gui_main_menu)
        print("    [build]  title card → gui/main_menu.png")

    print(f"    [build]  project written to: {output_dir}")
    result = {"project_dir": os.path.abspath(output_dir)}

    if sdk_path:
        from renpy._script import node_line_ranges
        ranges = node_line_ranges(full_script, node_ids)
        lint_summary = run_final_lint(output_dir, sdk_path, node_ranges=ranges)
        result["lint"] = lint_summary
        print(f"    [build]  final lint: {lint_summary['error_count']} error(s)")
        # Distribute (packaging) is expensive; skip it for mid-build compile checks
        # and only run it for final delivery.
        if distribute:
            result.update(_distribute(output_dir, sdk_path))
    else:
        result["lint"] = {"error_count": None}

    return {"status": "built", "output_dir": output_dir, **result}
