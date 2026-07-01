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
    assert job["workflow_override"]  # a concrete t2i graph, not empty
