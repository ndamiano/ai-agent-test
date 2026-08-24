import json

import pytest

from scenegen import layout
from scenegen.paintspec import fallback_spec, paint_spec


def scripted_llm(responses):
    """Each call pops the next canned response; the transcript stays inspectable."""
    calls = []

    def llm(messages, max_tokens=2048):
        calls.append(messages)
        return responses.pop(0)
    llm.calls = calls
    return llm


TILESET = {"terrain": [
    {"symbol": "G", "name": "grass", "walkable": True, "color": "#4caf50"},
    {"symbol": "W", "name": "water", "walkable": False, "color": "#2196f3"},
    {"symbol": "S", "name": "sand", "walkable": True, "color": "#f1c40f"}]}

ITEMS = {"objects": [{"symbol": "h", "name": "hut", "count": 1, "on_terrain": "G",
                      "walkable": False, "cells": 2}],
         "entities": [{"symbol": "f", "name": "fisherman", "count": 1, "on_terrain": "S"}]}


def _coarse(h, w):
    rows = ["G" * w for _ in range(h - 1)] + ["W" * w]
    return {"rows": rows}


def test_generate_layout_happy_path():
    llm = scripted_llm([
        json.dumps(TILESET),
        json.dumps(ITEMS),
        json.dumps(_coarse(6, 8)),
        json.dumps({"ops": [{"op": "line", "tile": "S", "from": [20, 0], "to": [20, 31]}]}),
        json.dumps({"placements": [{"symbol": "h", "row": 2, "col": 2},
                                   {"symbol": "f", "row": 20, "col": 5}]}),
    ])
    out = layout.generate_layout(llm, "a fishing village", 32, 24)
    assert len(out["grid"]) == 24 and len(out["grid"][0]) == 32
    assert out["grid"][20][5] == "S"
    assert {p["symbol"] for p in out["placements"]} == {"h", "f"}
    assert out["checks"]["largest_component_frac"] > 0.9
    assert out["walkable"][2][2] == "0" and out["walkable"][3][3] == "0"


def test_bad_json_is_reasked_with_the_error():
    llm = scripted_llm(["not json at all", json.dumps(TILESET)])
    terrain = layout.stage_tileset(llm, "a place")
    assert len(terrain) == 3
    reask = llm.calls[1]
    assert reask[-1]["role"] == "user"
    assert "problem" in reask[-1]["content"]


def test_validator_error_is_fed_back():
    bad = {"terrain": [{"symbol": "G", "name": "grass", "walkable": True, "color": "#4caf50"}]}
    llm = scripted_llm([json.dumps(bad), json.dumps(TILESET)])
    terrain = layout.stage_tileset(llm, "a place")
    assert len(terrain) == 3
    assert "3 to 8" in llm.calls[1][-1]["content"]


def test_exhausted_retries_raise():
    llm = scripted_llm(["nope", "nope", "nope"])
    with pytest.raises(RuntimeError):
        layout.stage_tileset(llm, "a place")


def test_garish_or_missing_tile_color_gets_a_fallback():
    t = {"terrain": [dict(TILESET["terrain"][0], color="magenta"),
                     dict(TILESET["terrain"][1], color=None),
                     TILESET["terrain"][2]]}
    llm = scripted_llm([json.dumps(t)])
    terrain = layout.stage_tileset(llm, "a place")
    assert terrain[0]["color"] == "#9e9e9e" and terrain[1]["color"] == "#9e9e9e"
    assert terrain[2]["color"] == "#f1c40f"


def test_upscale_and_smooth_is_deterministic_and_scaled():
    rows = ["GGGGWWWW", "GGGGWWWW", "GGGGWWWW", "GGGGWWWW", "GGGGWWWW", "GGGGWWWW"]
    a = layout.upscale_and_smooth(rows)
    b = layout.upscale_and_smooth(rows)
    assert a == b
    assert len(a) == 24 and len(a[0]) == 32
    assert a[0][0] == "G" and a[0][-1] == "W"


def test_placement_snaps_to_preferred_terrain():
    grid = ["W" * 32 if r < 12 else "G" * 32 for r in range(24)]
    terrain = TILESET["terrain"]
    items = [{"symbol": "h", "name": "hut", "count": 1, "on_terrain": "G",
              "walkable": False, "cells": 1, "kind": "object"}]
    llm = scripted_llm([json.dumps({"placements": [{"symbol": "h", "row": 0, "col": 0}]})])
    placements, snapped = layout.stage_place(llm, "p", terrain, items, grid)
    assert snapped == 1
    p = placements[0]
    assert grid[p["row"]][p["col"]] == "G"


def test_apply_ops_line_and_rect():
    grid = ["GGGG"] * 4
    out = layout.apply_ops(grid, [
        {"op": "line", "tile": "W", "from": [0, 0], "to": [3, 3]},
        {"op": "rect", "tile": "S", "top_left": [0, 3], "bottom_right": [1, 3],
         "fill": True}])
    assert out[0][0] == "W" and out[3][3] == "W"
    assert out[0][3] == "S" and out[1][3] == "S"


def test_paint_spec_covers_every_terrain_or_reasks():
    partial = {"terrains": [{"name": "grass", "color": "#6da24c", "phrase": "soft grass"}]}
    full = {"terrains": [
        {"name": "grass", "color": "#6da24c", "phrase": "soft grass"},
        {"name": "water", "color": "#39708f", "phrase": "calm deep water"},
        {"name": "sand", "color": "#dcc998", "phrase": "pale sand"}]}
    llm = scripted_llm([json.dumps(partial), json.dumps(full)])
    spec = paint_spec(llm, "a place", TILESET["terrain"])
    assert set(spec) == {"grass", "water", "sand"}
    assert "missing terrains" in llm.calls[1][-1]["content"]


def test_fallback_spec_uses_tileset_colors():
    spec = fallback_spec(TILESET["terrain"])
    assert spec["water"]["color"] == "#2196f3"
    assert spec["water"]["phrase"] == "water"
