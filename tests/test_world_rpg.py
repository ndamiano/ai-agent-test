"""Walkable RPG authoring: the `world` module emits/validates tile maps (tiles.legend/rows + cell
spawns) when `combat` is in the module set, and point-and-click rooms otherwise. Covers the spatial
validator (the loop's gate against a small model's bad layout), the return-trip connectivity gate,
the tool plumbing (set_places_meta start_spawn), the assemble lift (start.spawn + genre), and the
context-driven style selection."""
import copy
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from maestro.spec import Spec
from maestro.state import RunState
from maestro.tools import build_tools
from maestro.ir_assemble import assemble_ir
from maestro.modules.module import Error, ErrorType, load_prompt
from maestro.modules.world import (
    MODULE as WORLD, SKEL_RPG, SKEL_PLACES, _rpg_world_error, _return_path_error)


def _tiles(rows):
    return {"legend": {}, "rows": rows}


# A valid 4x3 interior: spawn (0,1), enemy tile (3,1), sign (1,1), two walls (default '#') that don't
# seal anyone (col 2 is open on the middle row).
_VALID = {
    "start_place": "z1", "start_spawn": {"cell": {"x": 0, "y": 1}},
    "place_ids": ["z1"],
    "places": {"z1": {
        "kind": "interior", "tiles": _tiles(["..#.", "....", "..#."]),
        "interactables": [
            {"id": "enemy", "label": "Foe", "position": {"cell": {"x": 3, "y": 1}},
             "action": {"type": "start_combat", "encounter": "e"}},
            {"id": "sign", "label": "Sign", "position": {"cell": {"x": 1, "y": 1}},
             "action": {"type": "examine", "text": "hi"}}]}}}


def _z1(**patch):
    v = copy.deepcopy(_VALID)
    v["places"]["z1"].update(patch)
    return v


# ── spatial validator ────────────────────────────────────────────────────────

def test_valid_walkable_map_passes():
    assert _rpg_world_error(_VALID) is None


def test_out_of_bounds_cell_caught():
    bad = _z1(interactables=[{"id": "x", "position": {"cell": {"x": 9, "y": 1}},
                             "action": {"type": "examine", "text": "t"}}])
    assert "outside the 4x3 grid" in _rpg_world_error(bad)


def test_all_layout_issues_reported_together():
    # Two independent defects (one out-of-bounds, one on a wall) surface in ONE message — reporting
    # them one at a time makes the model ping-pong.
    bad = _z1(interactables=[
        {"id": "oob", "position": {"cell": {"x": 9, "y": 1}},
         "action": {"type": "examine", "text": "t"}},
        {"id": "walled", "position": {"cell": {"x": 2, "y": 0}},
         "action": {"type": "examine", "text": "t"}}])
    msg = _rpg_world_error(bad)
    assert "outside the 4x3 grid" in msg and "on a blocked tile" in msg


def test_overlapping_tiles_caught():
    bad = _z1(interactables=[
        {"id": "a", "position": {"cell": {"x": 1, "y": 1}}, "action": {"type": "examine", "text": "t"}},
        {"id": "b", "position": {"cell": {"x": 1, "y": 1}}, "action": {"type": "examine", "text": "t"}}])
    assert "two interactables on tile" in _rpg_world_error(bad)


def test_interactable_on_blocked_tile_caught():
    # Re-paint so the enemy's tile (3,1) is a wall — an interactable can't sit on a blocked tile.
    assert "on a blocked tile" in _rpg_world_error(_z1(tiles=_tiles(["..#.", "...#", "..#."])))


def test_ragged_rows_caught():
    assert "ragged" in _rpg_world_error(_z1(tiles=_tiles(["....", "..", "...."])))


def test_unknown_char_caught():
    assert "no legend entry" in _rpg_world_error(_z1(tiles=_tiles(["..Q.", "....", "...."])))


def test_custom_legend_char_passes():
    v = _z1(tiles={"legend": {"W": {"role": "blocked", "theme": "hedge"}},
                   "rows": ["..W.", "....", "..W."]})
    assert _rpg_world_error(v) is None


def test_walled_off_interactable_caught():
    # A 5x1 corridor: spawn (0,0), enemy (4,0), a wall at (2,0) — enemy is NOT on a wall but is
    # unreachable. This is the load-bearing reachability check (schema can't see it).
    corridor = {
        "start_place": "z1", "start_spawn": {"cell": {"x": 0, "y": 0}},
        "place_ids": ["z1"],
        "places": {"z1": {"kind": "world_map", "tiles": _tiles(["..#.."]),
            "interactables": [
                {"id": "enemy", "position": {"cell": {"x": 4, "y": 0}},
                 "action": {"type": "examine", "text": "t"}}]}}}
    assert "walled off from the spawn" in _rpg_world_error(corridor)


def test_missing_tiles_caught():
    bad = copy.deepcopy(_VALID)
    del bad["places"]["z1"]["tiles"]
    assert "'tiles'" in _rpg_world_error(bad)


def test_walkable_start_place_needs_spawn():
    bad = copy.deepcopy(_VALID)
    del bad["start_spawn"]
    assert "walkable start place" in _rpg_world_error(bad)


def test_move_into_walkable_place_needs_spawn():
    two = copy.deepcopy(_VALID)
    two["place_ids"].append("z2")
    two["places"]["z2"] = {"kind": "interior", "tiles": _tiles(["...", "...", "..."]),
        "interactables": [{"id": "back", "position": {"cell": {"x": 0, "y": 0}},
                           "action": {"type": "examine", "text": "t"}}]}
    # a move from z1 -> z2 (walkable) WITHOUT a spawn must be rejected
    two["places"]["z1"]["interactables"].append(
        {"id": "door", "position": {"cell": {"x": 0, "y": 0}},
         "action": {"type": "move", "target": "z2"}})
    assert "no 'spawn'" in _rpg_world_error(two)


def test_pnc_room_is_untouched_by_rpg_checks():
    assert _rpg_world_error({"places": {"r": {"kind": "room", "interactables": [
        {"id": "h", "position": {"rect": {"x": 1, "y": 1, "w": 9, "h": 9}},
         "action": {"type": "examine", "text": "t"}}]}}}) is None


# ── return-trip connectivity ───────────────────────────────────────────────────

def _two_zone(z1_moves_back: bool):
    z1_inter = [{"id": "fwd", "position": {"cell": {"x": 1, "y": 0}},
                 "action": {"type": "move", "target": "z2", "spawn": {"cell": {"x": 0, "y": 0}}}}]
    z2_inter = [{"id": "sign", "position": {"cell": {"x": 1, "y": 0}},
                 "action": {"type": "examine", "text": "t"}}]
    if z1_moves_back:
        z2_inter.append({"id": "back", "position": {"cell": {"x": 0, "y": 0}},
                         "action": {"type": "move", "target": "z1", "spawn": {"cell": {"x": 0, "y": 0}}}})
    return {"start_place": "z1", "start_spawn": {"cell": {"x": 0, "y": 0}},
            "place_ids": ["z1", "z2"],
            "places": {
                "z1": {"kind": "world_map", "tiles": _tiles([".."]), "interactables": z1_inter},
                "z2": {"kind": "world_map", "tiles": _tiles([".."]), "interactables": z2_inter}}}


def test_one_way_trip_flagged():
    err = _return_path_error(_two_zone(z1_moves_back=False))
    assert err and "no way back" in err.lower() and "z2" in err


def test_round_trip_passes():
    assert _return_path_error(_two_zone(z1_moves_back=True)) is None


# ── tool plumbing: set_places_meta start_spawn ────────────────────────────────

def _spec():
    return Spec({"title": "T", "frozen": True, "modules": [], "params": {}})


def test_set_places_meta_stores_start_spawn(tmp_path):
    state = RunState(tmp_path)
    tools = build_tools(_spec(), state)
    res = tools["set_places_meta"](start_place="z1", start_spawn={"cell": {"x": 2, "y": 3}})
    assert res["ok"] and res["start_spawn"] == {"cell": {"x": 2, "y": 3}}
    assert state.read_component("places")["start_spawn"] == {"cell": {"x": 2, "y": 3}}


def test_set_places_meta_rejects_bad_spawn(tmp_path):
    tools = build_tools(_spec(), RunState(tmp_path))
    assert tools["set_places_meta"](start_spawn={"x": 1, "y": 2})["ok"] is False  # not a {cell:...}


# ── assemble: start.spawn + genre ─────────────────────────────────────────────

def test_assemble_lifts_start_spawn_and_genre():
    places = {"place_ids": ["z1"], "start_place": "z1",
              "start_spawn": {"cell": {"x": 1, "y": 2}},
              "places": {"z1": {"kind": "interior", "tiles": _tiles(["...", "...", "..."]),
                  "interactables": [{"id": "h", "position": {"cell": {"x": 0, "y": 0}},
                                     "action": {"type": "examine", "text": "t"}}]}}}
    ir = assemble_ir({"places": places, "characters": {"characters": []},
                      "asset_manifest": {"backgrounds": []}})
    assert ir["genre"] == "rpg"
    assert ir["start"] == {"place": "z1", "spawn": {"cell": {"x": 1, "y": 2}}}


def test_assemble_pnc_room_stays_point_and_click():
    places = {"place_ids": ["r1"], "start_place": "r1",
              "places": {"r1": {"kind": "room", "background": "bg", "interactables": [
                  {"id": "h", "position": {"rect": {"x": 1, "y": 1, "w": 9, "h": 9}},
                   "action": {"type": "examine", "text": "t"}}]}}}
    ir = assemble_ir({"places": places, "characters": {"characters": []},
                      "asset_manifest": {"backgrounds": [{"id": "bg", "image_file": "b.png"}]}})
    assert ir["genre"] == "point_and_click"
    assert "spawn" not in ir["start"]


# ── context-driven style selection (one mental model per prompt, no mixing) ────

class _Ctx:
    def __init__(self, modules, artifact=None):
        self.spec = {"modules": modules, "params": {}, "engine": "godot"}
        self.artifact = artifact or {}
        self.upstream_views = {}
        self.run_dir = None

    def param(self, name, default=None):
        return self.spec["params"].get(name, default)

    @property
    def engine(self):
        return "godot"


def test_combat_game_selects_rpg_skeleton_and_prompt():
    ctx = _Ctx(["world", "scenes", "combat"])
    author = WORLD._check_for("start_place")   # a style-dependent author check
    assert author.skeleton(ctx) is SKEL_RPG
    assert author.prompt(ctx) == "places_rpg_write.txt"


def test_non_combat_game_selects_pnc_skeleton_and_prompt():
    ctx = _Ctx(["world", "scenes"])
    author = WORLD._check_for("start_place")
    assert author.skeleton(ctx) is SKEL_PLACES
    assert author.prompt(ctx) == "places_write.txt"


def test_get_errors_emits_rpg_layout_for_broken_map():
    # a combat game whose only zone walls its enemy off — get_errors must surface rpg_layout
    art = {"places": {
        "start_place": "z1", "start_spawn": {"cell": {"x": 0, "y": 0}}, "place_ids": ["z1"],
        "places": {"z1": {"kind": "world_map", "tiles": _tiles(["..#.."]),
            "interactables": [
                {"id": "enemy", "position": {"cell": {"x": 4, "y": 0}},
                 "action": {"type": "start_combat", "encounter": "e"}},
                {"id": "sign", "position": {"cell": {"x": 1, "y": 0}},
                 "action": {"type": "examine", "text": "t"}}]}}}}
    ctx = _Ctx(["world", "scenes", "combat"], artifact=art)
    ctx.spec["params"] = {"min_places": 1, "min_interactables": 1}
    codes = [e.code for e in WORLD.get_errors(ctx)]
    assert "rpg_layout" in codes


def test_get_errors_emits_rpg_connectivity_for_one_way_map():
    art = {"places": _two_zone(z1_moves_back=False)}
    ctx = _Ctx(["world", "scenes", "combat"], artifact=art)
    ctx.spec["params"] = {"min_places": 1, "min_interactables": 1}
    codes = [e.code for e in WORLD.get_errors(ctx)]
    assert "rpg_connectivity" in codes


def test_combat_game_rejects_a_point_and_click_room():
    # a walkable (combat) game must be all-walkable — a `room` zone starts as a click screen.
    art = {"places": {
        "start_place": "z1", "start_spawn": {"cell": {"x": 0, "y": 0}}, "place_ids": ["z1", "r1"],
        "places": {
            "z1": {"kind": "world_map", "tiles": _tiles(["..."]), "interactables": [
                {"id": "a", "position": {"cell": {"x": 0, "y": 0}}, "action": {"type": "examine", "text": "t"}}]},
            "r1": {"kind": "room", "interactables": [
                {"id": "b", "position": {"rect": {"x": 1, "y": 1, "w": 9, "h": 9}},
                 "action": {"type": "examine", "text": "t"}}]}}}}
    ctx = _Ctx(["world", "scenes", "combat"], artifact=art)
    ctx.spec["params"] = {"min_places": 1, "min_interactables": 1}
    errs = [e for e in WORLD.get_errors(ctx) if e.code == "rpg_layout" and e.path == "r1"]
    assert errs and "room" in errs[0].message


def test_non_combat_game_allows_rooms():
    art = {"places": {
        "start_place": "r1", "place_ids": ["r1"],
        "places": {"r1": {"kind": "room", "interactables": [
            {"id": "b", "position": {"rect": {"x": 1, "y": 1, "w": 9, "h": 9}},
             "action": {"type": "examine", "text": "t"}}]}}}}
    ctx = _Ctx(["world", "scenes"], artifact=art)
    ctx.spec["params"] = {"min_places": 1, "min_interactables": 1}
    assert not [e for e in WORLD.get_errors(ctx) if e.code == "rpg_layout"]


def test_rpg_author_prompt_loads_with_includes_resolved():
    p = load_prompt("places_rpg_write.txt")
    assert "{{include" not in p and "start_combat" in p and "WASD" in p
