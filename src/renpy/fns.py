import os
import shutil
from pathlib import Path
from typing import Dict, List, Optional

from utils.image import write_solid_png

# (width, height, rgb) of the solid placeholder written when an asset fails to generate.
_PLACEHOLDER_SPECS = {
    "bg":         (1280, 720, (58, 58, 92)),
    "char":       (512, 768, (92, 58, 92)),
    "item":       (128, 128, (120, 100, 40)),
    "cg":         (1280, 720, (40, 20, 60)),
    "title_card": (1280, 720, (20, 30, 60)),
}


def _get_sdk_path() -> str:
    try:
        from config.settings_manager import settings_manager
        sdk = settings_manager.get_settings().get("renpy_sdk_path") or ""
        if sdk:
            return sdk
    except Exception:
        pass
    return os.environ.get("RENPY_SDK", "")


def _nodes_list(inputs: Dict) -> List[Dict]:
    nodes_comp = inputs.get("nodes", {}) or {}
    nodes_map = nodes_comp.get("nodes", {}) or {}
    return [{"id": nid, **(nodes_map.get(nid) or {})}
            for nid in (nodes_comp.get("node_ids", []) or [])]


_TILE_SLUG_CHARS = set("abcdefghijklmnopqrstuvwxyz0123456789")


def tile_slug(theme: str) -> str:
    """Filename slug for a tile theme (a walkable map's `theme` string) -> `tile_<slug>.png`. Kept
    byte-for-byte in sync with overworld.gd's _slug so the generator and the runtime agree on the
    filename: lowercase, [a-z0-9] kept, every other run collapses to one '_', ends trimmed."""
    out: List[str] = []
    prev_us = False
    for ch in theme.lower():
        if ch in _TILE_SLUG_CHARS:
            out.append(ch)
            prev_us = False
        elif not prev_us:
            out.append("_")
            prev_us = True
    return "".join(out).strip("_")


def _collect_tile_themes(places_comp: Dict) -> List[str]:
    """Every distinct tile `theme` a walkable map renders (legend entries + the default chars its
    rows actually use), so each gets one generated terrain texture. Empty for VN/PnC games."""
    return [t for t, _ in _collect_tile_specs(places_comp)]


def _collect_tile_specs(places_comp: Dict) -> List[tuple]:
    """(theme, role) pairs — the role steers the texture prompt so open ground and blocked
    obstacles READ differently at a glance (the texture is the avatar's only passability signal).
    Cells inside a feature footprint are excluded: the feature SPRITE covers them (the presenter
    draws base ground underneath), so 'a supply wagon' never becomes a nonsense terrain texture."""
    from maestro.modules.world import DEFAULT_LEGEND, _RPG_KINDS

    specs: Dict[str, str] = {}
    for place in ((places_comp or {}).get("places") or {}).values():
        if not isinstance(place, dict) or place.get("kind") not in _RPG_KINDS:
            continue
        tiles = place.get("tiles") or {}
        merged = {**DEFAULT_LEGEND, **(tiles.get("legend") or {})}
        covered = set()
        for fp in (place.get("footprints") or {}).values():
            if isinstance(fp, dict):
                for dy in range(int(fp.get("h", 0))):
                    for dx in range(int(fp.get("w", 0))):
                        covered.add((int(fp["x"]) + dx, int(fp["y"]) + dy))
        for y, row in enumerate(tiles.get("rows") or []):
            for x, ch in enumerate(row):
                if (x, y) in covered:
                    continue
                spec = merged.get(ch)
                if isinstance(spec, dict) and spec.get("theme"):
                    specs.setdefault(spec["theme"], spec.get("role", "open"))
    return list(specs.items())


def _collect_feature_specs(places_comp: Dict) -> List[tuple]:
    """(kind, label) per distinct solid footprint across walkable places — one object sprite
    each (feature_<slug>.png keyed on the label, so a 'smithy' in two zones shares art)."""
    from maestro.modules.world import _RPG_KINDS

    specs: Dict[str, str] = {}
    for place in ((places_comp or {}).get("places") or {}).values():
        if not isinstance(place, dict) or place.get("kind") not in _RPG_KINDS:
            continue
        for fp in (place.get("footprints") or {}).values():
            if isinstance(fp, dict) and fp.get("label") and tile_slug(fp["label"]):
                specs.setdefault(fp["label"], fp.get("kind", "building"))
    return [(kind, label) for label, kind in specs.items()]


def generate_images(inputs: Dict, working_dir: Path) -> Dict:
    from tools.comfyui_tools import (
        build_character_job, build_background_job, build_cg_job, build_item_job,
        build_title_card_job, build_character_emotion_job, build_tile_job, build_token_job,
        build_feature_job, make_seamless_tile, upload_image, vram_bracket, run_jobs,
    )
    from maestro.modules.world import _RPG_KINDS
    from maestro.ir_assemble import used_emotions, expression_file

    cast     = inputs.get("characters", {})
    manifest = _merge_cast_into_manifest(cast, inputs.get("asset_manifest", {}))

    images_dir = working_dir / "game_output" / "game" / "images"
    images_dir.mkdir(parents=True, exist_ok=True)

    cast_chars = {c["id"]: c for c in cast.get("characters", [])}
    nodes = _nodes_list(inputs)
    generated: List[str] = []
    failed:    List[Dict] = []

    def _place(meta: Dict, result: Dict) -> None:
        filepath, img_file, kind = meta["dest"], meta["file"], meta["kind"]
        if result.get("success") and result.get("saved_paths"):
            shutil.copy2(result["saved_paths"][0], filepath)
            generated.append(img_file)
            print(f"    [images]  ok: {img_file}")
            return
        error = result.get("error", "unknown")
        # Emotion variants degrade to the neutral face rather than a solid block; if the neutral
        # isn't on disk, compile-time placeholders still cover the reference. A missing token is
        # not placeholder-backed either — the overworld's colour-dot fallback beats a grey square.
        if kind == "emotion" and meta.get("neutral_dest") and meta["neutral_dest"].exists():
            shutil.copy2(meta["neutral_dest"], filepath)
            print(f"    [images]  failed ({error}), neutral fallback: {img_file}")
        elif kind not in ("emotion", "token"):
            w, h, color = _PLACEHOLDER_SPECS[kind]
            write_solid_png(filepath, w, h, color)
            print(f"    [images]  failed ({error}), placeholder: {img_file}")
        failed.append({"file": img_file, "error": error})

    # --- base pass: backgrounds, neutral character sprites, cgs, items, title card ----------
    base_meta: List[Dict] = []
    base_jobs: List[Dict] = []
    char_bases: List[Dict] = []  # for the img2img emotion pass

    for bg in manifest.get("backgrounds", []):
        bg_file = bg["image_file"]
        base_meta.append({"file": bg_file, "dest": images_dir / bg_file, "kind": "bg"})
        base_jobs.append(build_background_job(bg.get("description", bg.get("name", bg["id"]))))

    walkable = any(isinstance(p, dict) and p.get("kind") in _RPG_KINDS
                   for p in ((inputs.get("places") or {}).get("places") or {}).values())
    for char in manifest.get("characters", []):
        cid = char["id"]
        img_file = char.get("image_file", f"{cid}.png")
        merged = {**cast_chars.get(cid, {}), **char}
        base_meta.append({"file": img_file, "dest": images_dir / img_file, "kind": "char"})
        base_jobs.append(build_character_job(merged))
        char_bases.append({"id": cid, "char": merged, "base_file": img_file,
                           "dest": images_dir / img_file})
        if walkable:
            # Walkable maps draw people at ~1 tile — a shrunken VN portrait floats; a chibi
            # token reads. The overworld probes for <id>_token.png (avatar + talk markers).
            tok_file = f"{cid}_token.png"
            base_meta.append({"file": tok_file, "dest": images_dir / tok_file, "kind": "token"})
            base_jobs.append(build_token_job(merged))

    for cg in manifest.get("cgs", []):
        img_file = cg.get("image_file", f"{cg['id']}.png")
        base_meta.append({"file": img_file, "dest": images_dir / img_file, "kind": "cg"})
        base_jobs.append(build_cg_job(cg.get("description", cg["id"])))

    for it in manifest.get("items", []):
        img_file = it.get("image_file", f"{it['id']}.png")
        base_meta.append({"file": img_file, "dest": images_dir / img_file, "kind": "item"})
        base_jobs.append(build_item_job(it.get("description", it.get("name", it["id"]))))

    title_card = manifest.get("title_card", {})
    if title_card.get("description"):
        tc_file = title_card.get("image_file", "title_card.png")
        base_meta.append({"file": tc_file, "dest": images_dir / tc_file, "kind": "title_card"})
        base_jobs.append(build_title_card_job(title_card["description"]))

    # Marker assets: overworld hotspots draw a THING, not a colored diamond. One signpost per
    # game for exits, a small prop icon per examine hotspot (its label is the description), a
    # banner for win. All ride the item-icon pipeline; misses fall back to diamonds (kind token
    # semantics: no placeholder).
    if walkable:
        verbs = set()
        examine_labels: List[str] = []
        for p in ((inputs.get("places") or {}).get("places") or {}).values():
            if not isinstance(p, dict) or p.get("kind") not in _RPG_KINDS:
                continue
            for hot in p.get("interactables") or []:
                a = (hot or {}).get("action") or {}
                verbs.add(a.get("type"))
                if a.get("type") == "examine" and hot.get("label"):
                    examine_labels.append(hot["label"])
        if "move" in verbs:
            base_meta.append({"file": "marker_signpost.png",
                              "dest": images_dir / "marker_signpost.png", "kind": "token"})
            base_jobs.append(build_item_job("weathered wooden trail signpost with a blank arrow board"))
        if "win" in verbs:
            base_meta.append({"file": "marker_banner.png",
                              "dest": images_dir / "marker_banner.png", "kind": "token"})
            base_jobs.append(build_item_job("small victory banner on a standing pole"))
        for label in dict.fromkeys(examine_labels[:12]):
            pf = f"prop_{tile_slug(label)}.png"
            base_meta.append({"file": pf, "dest": images_dir / pf, "kind": "token"})
            base_jobs.append(build_item_job(label))
        # Feature sprites: one object sprite per distinct stamped footprint (kind+label),
        # drawn by the overworld over the footprint rect instead of a tile mosaic. Miss ->
        # the mosaic stays (kind token: no placeholder).
        for kind, label in _collect_feature_specs(inputs.get("places", {}))[:10]:
            ff = f"feature_{tile_slug(label)}.png"
            base_meta.append({"file": ff, "dest": images_dir / ff, "kind": "token"})
            base_jobs.append(build_feature_job(kind, label))

    with vram_bracket():
        print(f"    [images]  generating {len(base_jobs)} base image(s)")
        for meta, result in zip(base_meta, run_jobs(base_jobs)):
            _place(meta, result)

        # --- emotion pass: img2img each used expression off the character's neutral base -----
        emo_meta: List[Dict] = []
        emo_jobs: List[Dict] = []
        for cb in char_bases:
            if cb["base_file"] not in generated:
                continue  # neutral failed; compile-time placeholders cover its expressions
            emotions = [e for e in used_emotions(cb["id"], nodes) if e != "neutral"]
            if not emotions:
                continue
            try:
                base_name = upload_image(str(cb["dest"]))
            except Exception as e:
                print(f"    [images]  upload failed for {cb['base_file']}: {e}")
                continue
            for emotion in emotions:
                fname = expression_file(cb["base_file"], emotion)
                emo_meta.append({"file": fname, "dest": images_dir / fname,
                                 "kind": "emotion", "neutral_dest": cb["dest"]})
                emo_jobs.append(build_character_emotion_job(cb["char"], emotion, base_name))

        if emo_jobs:
            print(f"    [images]  generating {len(emo_jobs)} expression variant(s)")
            for meta, result in zip(emo_meta, run_jobs(emo_jobs)):
                _place(meta, result)

        # --- tile pass: one terrain texture per distinct walkable-map theme. On failure we skip
        # (no placeholder) so the overworld falls back to its computed theme colour — a solid block
        # is no better than the colour, and this keeps a no-ComfyUI build looking intentional. ----
        tile_specs = _collect_tile_specs(inputs.get("places", {}))
        if tile_specs:
            from tools.comfyui_tools import tile_refused
            print(f"    [images]  generating {len(tile_specs)} map tile(s)")
            # One tile at a time with a reseed loop: the ideogram endpoint's refusals are
            # seed-dependent (it bakes 'blocked by safety filter' INTO the image), so a
            # rejected pull just rolls again with a fresh seed.
            for theme, role in tile_specs:
                fname = f"tile_{tile_slug(theme)}.png"
                dest = images_dir / fname
                for attempt in range(4):
                    # 3 ideogram seed rolls, then DreamShaper — some theme phrases trip the
                    # refusal filter on every seed
                    job = build_tile_job(theme, role, ideogram=attempt < 3)
                    result = run_jobs([job])[0]
                    if not (result.get("success") and result.get("saved_paths")):
                        print(f"    [images]  tile failed ({result.get('error', 'unknown')}), "
                              f"colour fallback: {fname}")
                        break
                    if tile_refused(result["saved_paths"][0]):
                        print(f"    [images]  tile refused (seed roll {attempt + 1}): {fname}")
                        continue
                    shutil.copy2(result["saved_paths"][0], dest)
                    make_seamless_tile(dest)
                    generated.append(fname)
                    print(f"    [images]  ok: {fname}")
                    break

    return {"status": "ok", "generated": generated, "failed": failed}


def generate_voices(inputs: Dict, working_dir: Path) -> Dict:
    """Best-effort voice-over pass: one TTS clip per spoken line of a visual_novel, written to
    game/audio/voice/ where the `voice` statements ir_vn emits reference them. No-op unless a TTS
    server is configured (settings.tts) and the game is a VN. Wrapped by the caller so a failure
    never blocks delivery; any clip that fails degrades to a silent placeholder. Mirrors
    generate_images: assemble the IR, batch through the VRAM bracket, place or fall back."""
    from tools.tts_tools import voice_enabled, pick_voice, synthesize
    from tools.comfyui_tools import vram_bracket
    from maestro.ir_assemble import assemble_ir, voiced_lines, voice_file
    from utils.audio import write_silent_wav

    if not voice_enabled():
        return {"status": "skipped", "reason": "no tts endpoint configured"}

    ir = assemble_ir(inputs)
    if ir.get("genre") != "visual_novel":
        return {"status": "skipped", "reason": f"voice unsupported for genre {ir.get('genre')}"}

    voices = _tts_voices()
    char_voice = {c["id"]: pick_voice(c["id"], voices, sex=c.get("sex"),
                                      tts_voice=c.get("tts_voice"))
                  for c in ir.get("characters", [])}
    # Narration gets its own consistent voice (configurable), not the server default.
    narrator = (_tts_settings_narrator() or None)
    if narrator:
        char_voice[None] = narrator

    audio_dir = working_dir / "game_output" / "game" / "audio" / "voice"
    audio_dir.mkdir(parents=True, exist_ok=True)

    lines = list(voiced_lines(ir.get("nodes", [])))
    generated: List[str] = []
    failed: List[Dict] = []
    with vram_bracket():
        print(f"    [voice]  generating {len(lines)} clip(s)")
        for node_id, i, line in lines:
            fname = voice_file(node_id, i)
            dest = audio_dir / fname
            try:
                data = synthesize(line["text"], char_voice.get(line["speaker"]))
                dest.write_bytes(data)
                generated.append(fname)
            except Exception as e:
                write_silent_wav(dest)
                failed.append({"file": fname, "error": str(e)})
                print(f"    [voice]  failed ({e}), silent placeholder: {fname}")
    return {"status": "ok", "generated": generated, "failed": failed}


def _tts_voices() -> List[str]:
    try:
        from config.settings_manager import settings_manager
        return (settings_manager.get_settings().get("tts") or {}).get("voices", []) or []
    except Exception:
        return []


def _tts_settings_narrator() -> Optional[str]:
    try:
        from config.settings_manager import settings_manager
        return (settings_manager.get_settings().get("tts") or {}).get("narrator_voice")
    except Exception:
        return None


def _ensure_voice_placeholders(ir: Dict, game_dir: str) -> None:
    """Every spoken line the voiced VN script references must have an audio file on disk or Ren'Py
    lint flags it. The TTS pass fills these with real clips; where it didn't run or failed, write a
    silent placeholder. Mirrors _ensure_expression_placeholders for the per-line voice clips."""
    from maestro.ir_assemble import voiced_lines, voice_file
    from utils.audio import write_silent_wav
    audio_dir = Path(game_dir) / "audio" / "voice"
    for node_id, i, _line in voiced_lines(ir.get("nodes", [])):
        path = audio_dir / voice_file(node_id, i)
        if not path.exists():
            write_silent_wav(path)


def _merge_cast_into_manifest(cast: Dict, manifest: Dict) -> Dict:
    """Backfill a manifest entry for every character so sprite defines, placeholder pngs, image
    generation, and the lint's valid-speaker set (all keyed off asset_manifest.characters) cover
    the whole cast even when asset_manifest.characters is left incomplete. Existing manifest
    entries win as image overrides."""
    chars = list(manifest.get("characters", []))
    have = {c.get("id") for c in chars if isinstance(c, dict)}
    for pc in cast.get("characters", []):
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
    for it in manifest.get("items", []):
        _put(it.get("image_file", f"{it.get('id', '')}.png"), 128, 128, (120, 100, 40))
    tc = manifest.get("title_card", {})
    _put(tc.get("image_file", ""), 1280, 720, (20, 30, 60))


def _ensure_expression_placeholders(ir: Dict, game_dir: str) -> None:
    """Every per-emotion sprite the IR references must exist on disk or Ren'Py lint rejects it.
    img2img generation fills these with real art; where it didn't run or failed, degrade to the
    character's neutral sprite (a recognisable face) rather than a solid block. Mirrors
    _ensure_placeholder_images for the emotion variants, which aren't in the asset manifest."""
    images_dir = os.path.join(game_dir, "images")
    os.makedirs(images_dir, exist_ok=True)
    for c in ir.get("characters", []):
        neutral = c.get("sprite")
        for fname in (c.get("expressions") or {}).values():
            path = os.path.join(images_dir, fname)
            if os.path.exists(path):
                continue
            src = os.path.join(images_dir, neutral) if neutral else None
            if src and os.path.exists(src):
                shutil.copy2(src, path)
            else:
                write_solid_png(Path(path), 512, 768, (92, 58, 92))


# Project building + compile moved to renpy.ir_compiler (assemble_ir → ir_vn/ir_pnc → lint).
# fns now owns only image generation + the manifest backfills the compiler reuses.
