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


def test_build_tile_job_water_theme_overrides_role():
    from tools.comfyui_tools import build_tile_job

    for role in ("open", "blocked"):
        for theme in ("open sea", "tropical shallows", "reef", "glimmerpond"):
            cap = build_tile_job(theme, role=role)["prompt"]
            assert "water" in cap.lower(), (theme, role)
            assert "vegetation" not in cap.lower(), (theme, role)
            assert "walkable" not in cap.lower(), (theme, role)


def test_build_tile_job_water_dreamshaper_fallback():
    from tools.comfyui_tools import build_tile_job

    cap = build_tile_job("open sea", role="blocked", ideogram=False)["prompt"]
    assert "water surface" in cap
    assert "growth" not in cap


def test_ideogram_beach_gets_sand_palette():
    import json
    from tools.comfyui_tools import build_tile_job

    cap = json.loads(build_tile_job("beach", role="open")["prompt"])
    assert cap["style_description"]["color_palette"][0].startswith("#C9")


def test_ideogram_water_depth_palettes_differ():
    import json
    from tools.comfyui_tools import build_tile_job

    def pal(theme):
        return json.loads(build_tile_job(theme, role="blocked")["prompt"])[
            "style_description"]["color_palette"]

    assert pal("tropical shallows") != pal("open sea") != pal("reef")


# --- transition sheets: two adjacent map cells of DIFFERING themes get a texture the model
# draws AS ONE continuous surface (never blended in code) ------------------------------------

def test_build_transition_sheet_job_uses_ideogram_workflow():
    import json
    from tools.comfyui_tools import build_transition_sheet_job

    job = build_transition_sheet_job("beach", "tropical shallows")
    assert "endpoint" not in job                # single endpoint, no per-job routing
    cap = json.loads(job["prompt"])              # structured JSON caption, ideogram4's diet
    assert cap["aspect_ratio"] == "1:1"
    wf = job["workflow_override"]
    kinds = {v["class_type"] for v in wf.values()}
    assert {"DualModelGuider", "Ideogram4Scheduler", "CFGOverride"} <= kinds
    sc = next(v["inputs"] for v in wf.values() if v["class_type"] == "Ideogram4Scheduler")
    assert sc["width"] == sc["height"] == 1024


def test_ideogram_transition_caption_carries_both_themes_and_transition_language():
    import json
    from tools.comfyui_tools import _ideogram_transition_caption

    cap = json.loads(_ideogram_transition_caption("beach", "tropical shallows"))
    hld = cap["high_level_description"]
    bg = cap["compositional_deconstruction"]["background"]
    assert "beach" in hld and "tropical shallows" in hld
    assert "blending" in hld and "organically" in hld
    assert "beach" in bg and "tropical shallows" in bg
    assert "gradually" in bg or "organically" in bg


def test_ideogram_transition_caption_combines_palettes_when_both_match():
    import json
    from tools.comfyui_tools import _ideogram_transition_caption, _ideo_palette_for

    pal_a = _ideo_palette_for("beach")
    pal_b = _ideo_palette_for("tropical shallows")
    assert pal_a and pal_b and pal_a != pal_b   # both themes match a known palette

    cap = json.loads(_ideogram_transition_caption("beach", "tropical shallows"))
    palette = cap["style_description"]["color_palette"]
    assert palette[:len(pal_a)] == pal_a         # theme_a's hexes lead
    assert all(h in palette for h in pal_b)      # theme_b's hexes are folded in
    assert len(palette) <= 8

    # one theme with no keyword match -> no guess, just the matched side's palette
    solo = json.loads(_ideogram_transition_caption("beach", "chromatic void"))
    assert solo["style_description"]["color_palette"] == pal_a


def test_collect_tile_pairs_finds_adjacent_differing_themes():
    from renpy.fns import _collect_tile_pairs

    comp = {"places": {"z": {"kind": "world_map", "tiles": {
        "legend": {"@": {"role": "open", "theme": "beach"},
                   "^": {"role": "open", "theme": "tropical shallows"}},
        "rows": ["@@^", "@@^"]}}}}
    assert _collect_tile_pairs(comp) == [("beach", "tropical shallows")]


def test_collect_tile_pairs_skips_same_theme_adjacency():
    from renpy.fns import _collect_tile_pairs

    comp = {"places": {"z": {"kind": "world_map", "tiles": {
        "legend": {"@": {"role": "open", "theme": "beach"}},
        "rows": ["@@", "@@"]}}}}
    assert _collect_tile_pairs(comp) == []


def test_collect_tile_pairs_ignores_pnc_and_vn_and_dedupes_across_places():
    from renpy.fns import _collect_tile_pairs

    comp = {"places": {
        "z1": {"kind": "world_map", "tiles": {
            "legend": {"@": {"role": "open", "theme": "beach"},
                       "^": {"role": "open", "theme": "tropical shallows"}},
            "rows": ["@^"]}},
        "z2": {"kind": "town", "tiles": {
            "legend": {"@": {"role": "open", "theme": "beach"},
                       "^": {"role": "open", "theme": "tropical shallows"}},
            "rows": ["@^"]}},
        "r": {"kind": "room", "interactables": []},
    }}
    assert _collect_tile_pairs(comp) == [("beach", "tropical shallows")]


def test_slice_transition_sheet_produces_eight_named_variants(tmp_path):
    from PIL import Image
    from renpy.fns import slice_transition_sheet

    # A synthetic sheet with the same shape a real one has: a smooth left(red)->right(blue)
    # gradient across the full width, uniform down every column (no vertical variation) —
    # exactly what the boundary-parallel (y) wrap-blend is designed to leave untouched.
    size = 1024
    sheet = Image.new("RGB", (size, size))
    px = sheet.load()
    for x in range(size):
        r, b = round(255 * (1 - x / (size - 1))), round(255 * (x / (size - 1)))
        for y in range(size):
            px[x, y] = (r, 0, b)
    sheet_path = tmp_path / "sheet.png"
    sheet.save(sheet_path)

    written = slice_transition_sheet(sheet_path, "red", "blue", tmp_path)
    expected = {f"tile_red__blue_{d}.png" for d in "enws"} | \
               {f"tile_blue__red_{d}.png" for d in "enws"}
    assert set(written) == expected
    assert len(written) == 8
    for fname in written:
        assert Image.open(tmp_path / fname).size == (256, 256)

    def half_means(img):
        w, h = img.size
        def mean(box):
            data = list(img.crop(box).getdata())
            return tuple(sum(p[i] for p in data) / len(data) for i in range(3))
        return mean((0, 0, w // 2, h)), mean((w // 2, 0, w, h))

    # tile_red__blue_e: mostly A(red), B(blue) bleeding in toward its right/east edge
    left, right = half_means(Image.open(tmp_path / "tile_red__blue_e.png").convert("RGB"))
    assert left[0] > right[0]     # red fades out left->right
    assert left[2] < right[2]     # blue bleeds in toward the east edge

    # tile_blue__red_w: mostly B(blue), A(red) bleeding in toward its left/west edge
    left, right = half_means(Image.open(tmp_path / "tile_blue__red_w.png").convert("RGB"))
    assert left[0] > right[0]     # the red fade sits at this tile's west (left) edge
    assert left[2] < right[2]
