import os
import re
import shutil
from pathlib import Path
from typing import Dict, List

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
    manifest = _merge_items_into_manifest(inputs.get("places", {}), manifest)

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

    for it in manifest.get("items", []):
        img_file = it.get("image_file", f"{it['id']}.png")
        job_meta.append({"file": img_file, "dest": images_dir / img_file, "kind": "item"})
        jobs.append(build_background_job(it.get("description", it.get("name", it["id"]))))

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
            elif kind == "item":
                w, h, color = 128, 128, (120, 100, 40)
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


_APPEND_ITEM_RE = re.compile(r'inventory\.append\(\s*[\'"](\w+)[\'"]')


def _merge_items_into_manifest(rooms_art: Dict, manifest: Dict) -> Dict:
    """rooms.items is the source of truth for inventory items. The image defines and
    placeholder/generated icons key off asset_manifest.items, so backfill a manifest entry
    for every rooms item the agent didn't already list (existing entries win as overrides).
    Also backfill any item id the hotspot logic actually picks up (inventory.append) even if it
    was never declared — the inventory bar `add`s that image at runtime and would crash without
    a define. Mirrors _merge_cast_into_manifest for the cast."""
    if not rooms_art:
        return manifest
    items = list(manifest.get("items", []))
    have = {i.get("id") for i in items if isinstance(i, dict)}
    declared = {it.get("id"): it for it in rooms_art.get("items", []) if isinstance(it, dict)}

    picked_up = set()
    for room in (rooms_art.get("rooms", {}) or {}).values():
        for h in room.get("hotspots", []) if isinstance(room, dict) else []:
            picked_up |= set(_APPEND_ITEM_RE.findall(h.get("logic", "") or ""))

    for iid in list(declared) + sorted(picked_up):
        if iid and iid not in have:
            meta = declared.get(iid, {})
            items.append({"id": iid, "image_file": f"{iid}.png",
                          "description": meta.get("name") or meta.get("examine") or iid})
            have.add(iid)
    return {**manifest, "items": items}


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
    for it in manifest.get("items", []):
        _put(it.get("image_file", f"{it.get('id', '')}.png"), 128, 128, (120, 100, 40))
    tc = manifest.get("title_card", {})
    _put(tc.get("image_file", ""), 1280, 720, (20, 30, 60))


# Project building + compile moved to renpy.ir_compiler (assemble_ir → ir_vn/ir_pnc → lint).
# fns now owns only image generation + the manifest backfills the compiler reuses.
