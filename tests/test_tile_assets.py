"""Generated tile art: the theme -> filename slug (kept in sync with overworld.gd's _slug) and the
collection of distinct themes a walkable map renders (legend entries + the default chars its rows
use), which drives one t2i job per terrain type."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from renpy.fns import tile_slug, _collect_tile_themes
from tools.comfyui_tools import build_tile_job


def test_tile_slug_matches_gdscript_rule():
    assert tile_slug("ash-choked pine") == "ash_choked_pine"
    assert tile_slug("Frozen  Stream!!") == "frozen_stream"
    assert tile_slug("  temple stone  ") == "temple_stone"
    assert tile_slug("water") == "water"
    assert tile_slug("!!!") == ""


def test_collect_themes_includes_legend_and_used_defaults():
    comp = {"places": {"z": {"kind": "world_map", "tiles": {
        "legend": {"@": {"role": "blocked", "theme": "ash pine"}},
        "rows": ["@.,", "..#"]}}}}
    # '@' -> legend theme; '.' -> default ground; ',' -> default path; '#' -> default wall
    assert sorted(_collect_tile_themes(comp)) == ["ash pine", "ground", "path", "wall"]


def test_collect_themes_ignores_unused_defaults():
    comp = {"places": {"z": {"kind": "world_map",
                             "tiles": {"legend": {}, "rows": ["..", ".."]}}}}
    assert _collect_tile_themes(comp) == ["ground"]  # only '.' is used; not path/wall/tree/...


def test_collect_themes_empty_for_pnc_and_vn():
    assert _collect_tile_themes({"places": {"r": {"kind": "room", "interactables": []}}}) == []
    assert _collect_tile_themes({}) == []


def test_build_tile_job_dreamshaper_carries_theme_and_workflow():
    job = build_tile_job("frozen stream", ideogram=False)
    assert "frozen stream" in job["prompt"] and "tileable" in job["prompt"]
    assert "map tile" not in job["prompt"]   # "map tile" makes the model draw a picture OF a map
    assert "endpoint" not in job             # single endpoint, no per-job routing
    wf = job["workflow_override"]
    dims = next(v["inputs"] for v in wf.values() if v["class_type"] == "EmptyLatentImage")
    assert dims["width"] == dims["height"]   # square: cells render square, widescreen mushes


def test_build_tile_job_role_steers_readability():
    # lab-derived formulas (2026-07-04): open = stylized walkable tileset; wall-ish blocked
    # themes get FRONT-FACING masonry (top-down walls render as ground); organic blocked gets
    # dense-growth language darker than open ground
    open_p = build_tile_job("mossy ground", "open", ideogram=False)["prompt"]
    wall_p = build_tile_job("crumbling wall", "blocked", ideogram=False)["prompt"]
    bush_p = build_tile_job("dense brambles", "blocked", ideogram=False)["prompt"]
    assert "walkable" in open_p
    assert "front-facing" in wall_p and "impassable barrier" in wall_p
    assert "impassable growth" in bush_p and "front-facing" not in bush_p


def test_build_tile_job_ideogram_false_forces_dreamshaper():
    job = build_tile_job("dusty track", "open", ideogram=False)
    assert "endpoint" not in job and "tileable" in job["prompt"]


def test_build_tile_job_defaults_to_ideogram():
    import json
    job = build_tile_job("crumbling wall", "blocked")
    assert "endpoint" not in job             # single endpoint, no per-job routing
    cap = json.loads(job["prompt"])           # structured JSON caption, ideogram4's training diet
    assert cap["aspect_ratio"] == "1:1"
    assert "crumbling wall" in cap["high_level_description"]
    assert "impassable barrier" in cap["compositional_deconstruction"]["background"]
    wf = job["workflow_override"]
    kinds = {v["class_type"] for v in wf.values()}
    assert {"DualModelGuider", "Ideogram4Scheduler", "CFGOverride"} <= kinds
    sc = next(v["inputs"] for v in wf.values() if v["class_type"] == "Ideogram4Scheduler")
    assert sc["width"] == sc["height"]


def test_ideogram_caption_palette_matched_or_omitted():
    import json
    from tools.comfyui_tools import _ideogram_tile_caption
    dirt = json.loads(_ideogram_tile_caption("packed dirt trail", "open"))
    assert all(c.startswith("#") and len(c) == 7
               for c in dirt["style_description"]["color_palette"])
    odd = json.loads(_ideogram_tile_caption("chromatic void", "open"))
    assert "color_palette" not in odd["style_description"]   # no keyword match -> no guess


def test_collect_tile_specs_carries_roles():
    from renpy.fns import _collect_tile_specs
    comp = {"places": {"z": {"kind": "world_map", "tiles": {
        "legend": {"@": {"role": "blocked", "theme": "ash pine"}},
        "rows": ["@.", ".."]}}}}
    assert dict(_collect_tile_specs(comp)) == {"ash pine": "blocked", "ground": "open"}


def test_collect_tile_specs_skips_sprite_covered_footprint_cells():
    from renpy.fns import _collect_tile_specs
    comp = {"places": {"z": {"kind": "town",
        "tiles": {"legend": {"B": {"role": "blocked", "theme": "supply wagon"},
                             "C": {"role": "blocked", "theme": "gallows"}},
                  "rows": ["BB.", "CC."]},
        "footprints": {"f_w": {"x": 0, "y": 0, "w": 2, "h": 1,
                               "kind": "building", "label": "wagon"}}}}}
    themes = dict(_collect_tile_specs(comp["places"] and comp))
    # wagon cells are sprite-covered -> no terrain texture; gallows has no footprint -> kept
    assert "supply wagon" not in themes and "gallows" in themes and "ground" in themes


def test_collect_feature_specs_dedupes_by_label():
    from renpy.fns import _collect_feature_specs
    comp = {"places": {
        "z1": {"kind": "town", "footprints": {
            "f_a": {"x": 1, "y": 1, "w": 3, "h": 3, "kind": "building", "label": "smithy"},
            "f_b": {"x": 5, "y": 5, "w": 3, "h": 3, "kind": "fountain", "label": "fountain"}}},
        "z2": {"kind": "world_map", "footprints": {
            "f_c": {"x": 2, "y": 2, "w": 3, "h": 3, "kind": "building", "label": "smithy"},
            "f_d": {"x": 4, "y": 4, "w": 2, "h": 2, "kind": "market_stall", "label": "!!!"}}},
        "r": {"kind": "room"},
    }}
    specs = dict((label, kind) for kind, label in _collect_feature_specs(comp))
    # shared label -> one sprite; unsluggable label dropped; rooms ignored
    assert specs == {"smithy": "building", "fountain": "fountain"}


def test_build_feature_job_is_isolated_object():
    from tools.comfyui_tools import build_feature_job
    # The saved styled prompt IS the subject and LEADS the positive (CLIP weights early tokens
    # hardest); the builder adds only quality tags — never embeds it mid-phrase (grammar garble
    # + CLIP-window overflow drove subject drift). Framing/isolation prose lives in
    # asset_feature.txt, the climbable template.
    job = build_feature_job("building", "A stone smithy with a smoking chimney.")
    assert job["prompt"] == "A stone smithy with a smoking chimney."
    # kind is only the no-label fallback
    assert build_feature_job("market_stall", "")["prompt"] == "market stall"
    # rides the matting workflow so it lands transparent on the map
    kinds = {v["class_type"] for v in job["workflow_override"].values()}
    assert any("BiRefNet" in k for k in kinds)


def test_tile_refused_detects_flat_cards_and_transparency(tmp_path):
    import random
    from PIL import Image, ImageDraw
    from tools.comfyui_tools import tile_refused
    rng = random.Random(7)

    def texture(base):
        # grain + coarse tonal patches — pure per-pixel noise averages to a flat card at the
        # detector's low-frequency scale, which no real generated texture does
        im = Image.new("RGB", (512, 512))
        im.putdata([tuple(min(255, c + ((x // 64 + y // 64) % 3) * 12 + rng.randrange(-20, 20))
                          for c in base)
                    for y in range(512) for x in range(512)])
        return im

    dirt = (110, 88, 55)

    # refusal: bold white text across the center of an otherwise good texture (observed mode)
    card = texture(dirt)
    d = ImageDraw.Draw(card)
    for dx in range(3):
        for dy in range(3):
            d.text((100 + dx, 250 + dy), "Image blocked by safety filter",
                   fill=(255, 255, 255))
    p1 = tmp_path / "card.png"
    card.save(p1)
    assert tile_refused(p1)
    # near-transparent frame (second observed refusal mode)
    ghost = Image.new("RGBA", (512, 512), (0, 0, 0, 0))
    p2 = tmp_path / "ghost.png"
    ghost.save(p2)
    assert tile_refused(p2)
    # real textures pass, including low-contrast and bright-everywhere (snow) ones
    p3 = tmp_path / "tex.png"
    texture(dirt).save(p3)
    assert not tile_refused(p3)
    p4 = tmp_path / "snow.png"
    texture((225, 225, 230)).save(p4)
    assert not tile_refused(p4)


def test_make_seamless_tile_wraps_and_downscales(tmp_path):
    from PIL import Image
    from tools.comfyui_tools import make_seamless_tile
    # A locally-smooth gradient whose opposite edges differ by the full range (0 vs 255): tiled
    # untreated, that's the worst realistic wrap seam. (Generated textures are locally smooth —
    # the method's guarantee is wrap-continuity wherever the input is locally continuous.)
    grad = Image.linear_gradient("L").resize((512, 512)).transpose(Image.ROTATE_90)
    p = tmp_path / "t.png"
    grad.convert("RGB").save(p)
    img = Image.open(p)
    assert abs(img.load()[0, 0][0] - img.load()[511, 0][0]) > 200   # raw edges clash hard
    make_seamless_tile(str(p), out_size=128)
    out = Image.open(p)
    assert out.size == (128, 128)
    px = out.load()
    for i in range(0, 128, 8):
        l, r = px[0, i], px[127, i]
        assert abs(l[0] - r[0]) < 30, f"horizontal wrap seam at row {i}: {l} vs {r}"
        t, b = px[i, 0], px[i, 127]
        assert abs(t[0] - b[0]) < 30, f"vertical wrap seam at col {i}: {t} vs {b}"


def test_require_trellis_raises_loudly_when_server_down(monkeypatch):
    # There is deliberately NO fallback mesh generator: a dead trellis produced 34 silent
    # gray shape-only meshes on a live build. The error must be actionable (launch command).
    import pytest
    import tools.comfyui_tools as ct
    monkeypatch.setattr(ct, "_trellis_healthy", lambda ep: False)
    with pytest.raises(ct.MeshBackendError, match="trellis_server"):
        ct.require_trellis()
    monkeypatch.setattr(ct, "_trellis_healthy", lambda ep: True)
    ct.require_trellis()   # healthy -> no raise


def test_run_trellis_batch_posts_sprites_and_writes_glbs(monkeypatch, tmp_path):
    import tools.comfyui_tools as ct
    in_dir = tmp_path / "in"
    in_dir.mkdir()
    out = tmp_path / "out"
    out.mkdir()
    (in_dir / "a.png").write_bytes(b"pnga")
    (in_dir / "b.png").write_bytes(b"pngb")
    monkeypatch.setattr(ct, "_get_trellis_settings", lambda: {"endpoint": "http://x"})
    monkeypatch.setattr(ct, "_comfyui_free_vram", lambda *a, **k: None)
    monkeypatch.setattr(ct, "_llm_get_loaded_model", lambda: None)
    posted = []

    def fake_post(url, body, content_type, timeout=1200):
        posted.append(url)
        if url.endswith("/generate"):
            return b"GLB" + body            # echo so we can tell them apart
        return b""                          # /unload

    monkeypatch.setattr(ct, "_http_post_raw", fake_post)
    done = ct.run_trellis_batch(str(in_dir), str(out))
    assert done == {"a", "b"}
    assert (out / "a.glb").read_bytes() == b"GLBpnga"
    assert "http://x/unload" in posted    # VRAM released after the batch


def test_trellis_backend_leaves_texture_to_presenter():
    # contract: the overworld3d presenter must KEEP a glb's baked texture and skip sprite
    # projection when the mesh is textured (TRELLIS) — guard the gd code carries the check
    from pathlib import Path
    gd = (Path(__file__).parent.parent / "src/godot/runtime/overworld3d.gd").read_text()
    assert "_has_baked_texture" in gd
    load = gd.split("func _load_glb(")[1].split("\nfunc ")[0]
    assert "_has_baked_texture" in load and load.index("_has_baked_texture") < load.index("triplanar")
