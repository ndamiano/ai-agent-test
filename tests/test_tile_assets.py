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


def test_build_tile_job_carries_theme_and_workflow():
    job = build_tile_job("frozen stream")
    assert "frozen stream" in job["prompt"] and "tileable" in job["prompt"]
    assert "map tile" not in job["prompt"]   # "map tile" makes the model draw a picture OF a map
    wf = job["workflow_override"]
    dims = next(v["inputs"] for v in wf.values() if v["class_type"] == "EmptyLatentImage")
    assert dims["width"] == dims["height"]   # square: cells render square, widescreen mushes


def test_build_tile_job_role_steers_readability():
    # lab-derived formulas (2026-07-04): open = stylized walkable tileset; wall-ish blocked
    # themes get FRONT-FACING masonry (top-down walls render as ground); organic blocked gets
    # dense-growth language darker than open ground
    open_p = build_tile_job("mossy ground", "open")["prompt"]
    wall_p = build_tile_job("crumbling wall", "blocked")["prompt"]
    bush_p = build_tile_job("dense brambles", "blocked")["prompt"]
    assert "walkable" in open_p
    assert "front-facing" in wall_p and "impassable barrier" in wall_p
    assert "impassable growth" in bush_p and "front-facing" not in bush_p


def test_collect_tile_specs_carries_roles():
    from renpy.fns import _collect_tile_specs
    comp = {"places": {"z": {"kind": "world_map", "tiles": {
        "legend": {"@": {"role": "blocked", "theme": "ash pine"}},
        "rows": ["@.", ".."]}}}}
    assert dict(_collect_tile_specs(comp)) == {"ash pine": "blocked", "ground": "open"}


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
