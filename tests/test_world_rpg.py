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


def test_phantom_start_place_flagged_and_not_seeded():
    # start_place set to an id that was never authored: the check must name it, and the
    # reachability walk must not seed it (its message told the model to wire hotspots into a
    # nonexistent place — live-build loop poison).
    from maestro.modules import views
    from maestro.modules.context import Context

    class _S:
        run_dir = "/tmp/none"
        def load_artifact(self): return {}
        def read_story_state(self): return {}
        def read_scratchpad(self): return {}

    art = {"places": {"start_place": "phantom", "place_ids": ["p1"],
                      "places": {"p1": {"kind": "room", "interactables": [
                          {"id": "h1", "action": {"type": "examine", "text": "t"}}]}}}}
    chk = next(c for c in WORLD.checks if c.code == "start_authored")
    errs = chk.detect(chk, WORLD, Context(spec={"params": {}}, state=_S(), artifact=art))
    assert errs and "phantom" in errs[0].message and "p1" in errs[0].message
    assert views.reachable_places(["p1"], art["places"]["places"], "phantom") == {"p1"}


def test_bad_arrival_spawn_names_the_source_move():
    # The spawn is DECLARED on a move hotspot in another zone; a message naming only the
    # destination sends the model rewriting the wrong place (live-build thrash).
    two = copy.deepcopy(_VALID)
    two["place_ids"] = ["z1", "z2"]
    two["places"]["z2"] = {"kind": "interior", "tiles": _tiles(["....", "....", "...."]),
                           "interactables": [{"id": "sign", "position": {"cell": {"x": 1, "y": 1}},
                                              "action": {"type": "examine", "text": "t"}}]}
    two["places"]["z1"]["interactables"].append(
        {"id": "h_to_z2", "label": "door", "position": {"cell": {"x": 2, "y": 1}},
         "action": {"type": "move", "target": "z2", "spawn": {"cell": {"x": 9, "y": 9}}}})
    msg = _rpg_world_error(two)
    assert "'h_to_z2'" in msg and "'z1'" in msg   # the source move, not just the destination


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


def test_map_builder_rasterizes_connected_layout():
    from maestro.map_builder import build_tiles, v_layout
    layout = {
        "size": "medium",
        "terrain": {"open": "mossy earth", "blocked": "bone-pale cliff"},
        "features": [
            {"id": "f_smithy", "kind": "building", "at": "northwest", "theme": "timber smithy"},
            {"id": "f_fountain", "kind": "fountain", "at": "center"},
        ],
        "exits": [{"id": "x_south", "edge": "south"}],
        "connections": [{"from": "x_south", "to": "f_fountain"},
                        {"from": "f_fountain", "to": "f_smithy"}],
    }
    assert v_layout(layout) is None
    built = build_tiles("zone_town", layout)
    rows, legend, anchors = built["rows"], built["legend"], built["anchors"]
    w, h = len(rows[0]), len(rows)
    assert all(len(r) == w for r in rows)
    assert {"f_smithy", "f_fountain", "x_south"} <= set(anchors)
    # determinism: same zone id -> same map
    assert build_tiles("zone_town", layout)["rows"] == rows
    # every anchor is an open cell, all mutually reachable on open tiles
    open_roles = {ch for ch, e in legend.items() if e["role"] == "open"}

    def is_open(x, y):
        return 0 <= x < w and 0 <= y < h and rows[y][x] in open_roles

    from collections import deque
    ax0 = list(anchors.values())[0]
    seen = {(ax0["x"], ax0["y"])}
    q = deque(seen)
    while q:
        x, y = q.popleft()
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nx, ny = x + dx, y + dy
            if is_open(nx, ny) and (nx, ny) not in seen:
                seen.add((nx, ny))
                q.append((nx, ny))
    for aid, a in anchors.items():
        assert is_open(a["x"], a["y"]), f"{aid} anchor blocked"
        assert (a["x"], a["y"]) in seen, f"{aid} unreachable"
    # not a mud-box: paths exist and open ground is broken up
    assert any("," in r for r in rows)


def test_map_builder_emits_footprints_for_solid_features():
    from maestro.map_builder import build_tiles
    layout = {
        "size": "medium",
        "terrain": {"open": "grass", "blocked": "rock"},
        "features": [
            {"id": "f_smithy", "kind": "building", "at": "northwest",
             "theme": "timber smithy", "label": "smithy"},
            {"id": "f_glade", "kind": "clearing", "at": "center"},
        ],
        "exits": [{"id": "x_south", "edge": "south"}],
    }
    built = build_tiles("zone_fp", layout)
    fps = built["footprints"]
    assert "f_glade" not in fps and "x_south" not in fps   # nothing solid stamped
    fp = fps["f_smithy"]
    assert fp["kind"] == "building" and fp["label"] == "smithy"
    w, h = len(built["rows"][0]), len(built["rows"])
    assert 0 < fp["x"] and fp["x"] + fp["w"] <= w - 1
    assert 0 < fp["y"] and fp["y"] + fp["h"] <= h - 1
    # label falls back to theme, then kind
    layout["features"][0].pop("label")
    assert build_tiles("zone_fp", layout)["footprints"]["f_smithy"]["label"] == "timber smithy"


def test_action_walks_tolerate_malformed_use_shapes():
    # a transient string fallback (model edit between validated writes) crashed the state
    # scan and took the whole build process down — the walk must skip, never raise
    from maestro.modules.views import action_effects, action_conditions
    a = {"type": "use", "clauses": [{"requires": {"item": "k"}, "outcome": "not a dict"},
                                    "not a clause"],
         "fallback": "just a string"}
    assert list(action_effects(a)) == []
    assert list(action_conditions(a)) == [{"item": "k"}]


def test_talk_entered_nodes_are_reachability_roots():
    from maestro.modules.scenes import reachable_from_start
    art = {"nodes": {"node_ids": ["scene_01", "node_rally"], "nodes": {
        "scene_01": {"lines": [{"speaker": "a", "text": "x"}], "end": {"type": "return"}},
        "node_rally": {"lines": [{"speaker": "a", "text": "y"}], "end": {"type": "return"}}}},
        "places": {"places": {"town": {"kind": "town", "interactables": [
            {"id": "h_rally", "action": {"type": "talk", "node": "node_rally"}}]}}}}
    ok, msg = reachable_from_start(art)
    assert ok, msg   # node_rally is entered from the world, not an orphan
    art["places"]["places"]["town"]["interactables"] = []
    ok, msg = reachable_from_start(art)
    assert not ok and "node_rally" in msg


def test_snap_to_open_avoids_occupied_cells():
    from maestro.map_builder import snap_to_open
    tiles = {"legend": {}, "rows": ["...", "...", "..."]}
    # target cell free -> stays; occupied -> nearest free open cell (observed: two takes on
    # one tile parked a build after 6 blind LLM fixes)
    assert snap_to_open(tiles, 1, 1) == (1, 1)
    assert snap_to_open(tiles, 1, 1, occupied={(1, 1)}) != (1, 1)
    everywhere = {(x, y) for x in range(3) for y in range(3)}
    assert snap_to_open(tiles, 1, 1, occupied=everywhere) == (1, 1)  # all taken -> nearest open


def test_map_builder_rejects_bad_layout():
    from maestro.map_builder import v_layout
    assert v_layout({"size": "huge"}) is not None
    assert "kind" in v_layout({"size": "small", "features": [
        {"id": "f", "kind": "castle", "at": "center"}]})
    assert "at" in v_layout({"size": "small", "features": [
        {"id": "f", "kind": "building", "at": "middle"}]})


def test_write_place_rasterizes_layout_and_resolves_spawns(tmp_path):
    from maestro.spec import Spec
    from maestro.state import RunState
    from maestro.tools import build_tools

    state = RunState(tmp_path)
    spec = Spec({"title": "T", "frozen": True, "modules": ["world"], "params": {}})
    tools = build_tools(spec, state)
    layout_a = {"size": "small", "terrain": {"open": "grass", "blocked": "rock"},
                "features": [{"id": "f_camp", "kind": "camp", "at": "center"}],
                "exits": [{"id": "x_east", "edge": "east"}],
                "connections": [{"from": "x_east", "to": "f_camp"}]}
    res = tools["write_place"]("zone_a", {
        "kind": "world_map", "layout": layout_a,
        "interactables": [
            {"id": "h_camp", "label": "camp", "position": {"feature": "f_camp"},
             "action": {"type": "examine", "text": "cold fire pit"}},
            {"id": "h_go", "label": "east road", "position": {"feature": "x_east"},
             "action": {"type": "move", "target": "zone_b",
                        "spawn": {"feature": "x_west"}}}]})
    assert res["ok"] is True, res
    pa = state.read_component("places")["places"]["zone_a"]
    assert pa["tiles"]["rows"] and pa["anchors"]["f_camp"]
    assert pa["interactables"][0]["position"]["cell"]  # feature resolved to a cell
    # spawn still a feature ref — zone_b not written yet
    assert pa["interactables"][1]["action"]["spawn"] == {"feature": "x_west"}

    layout_b = {"size": "small", "terrain": {"open": "grass", "blocked": "rock"},
                "features": [{"id": "f_rock", "kind": "rock_outcrop", "at": "center"}],
                "exits": [{"id": "x_west", "edge": "west"}],
                "connections": [{"from": "x_west", "to": "f_rock"}]}
    res = tools["write_place"]("zone_b", {
        "kind": "world_map", "layout": layout_b,
        "interactables": [{"id": "h_back", "label": "west road",
                           "position": {"feature": "x_west"},
                           "action": {"type": "move", "target": "zone_a",
                                      "spawn": {"feature": "x_east"}}}]})
    assert res["ok"] is True, res
    pb = state.read_component("places")["places"]["zone_b"]
    assert "cell" in pb["interactables"][0]["action"]["spawn"]  # zone_a already known
    pa = state.read_component("places")["places"]["zone_a"]
    spawn = pa["interactables"][1]["action"]["spawn"]
    assert "cell" in spawn  # resolved the moment the target zone landed

    # unknown feature is refused with the declared ids in the message
    res = tools["write_place"]("zone_c", {
        "kind": "world_map", "layout": layout_b,
        "interactables": [{"id": "h_x", "label": "x", "position": {"feature": "f_nope"},
                           "action": {"type": "examine", "text": "t"}}]})
    assert res["ok"] is False and "f_nope" in res["error"]


def test_add_interactable_snaps_to_open_and_takes_features(tmp_path):
    from maestro.spec import Spec
    from maestro.state import RunState
    from maestro.tools import build_tools

    state = RunState(tmp_path)
    spec = Spec({"title": "T", "frozen": True, "modules": ["world"], "params": {}})
    tools = build_tools(spec, state)
    layout = {"size": "small", "terrain": {"open": "grass", "blocked": "rock"},
              "features": [{"id": "f_camp", "kind": "camp", "at": "center"}],
              "exits": [{"id": "x_east", "edge": "east"}]}
    tools["write_place"]("zone_a", {"kind": "world_map", "layout": layout, "interactables": [
        {"id": "h_c", "label": "camp", "position": {"feature": "f_camp"},
         "action": {"type": "examine", "text": "t"}}]})
    # raw cell on a border wall snaps to the nearest open tile
    res = tools["add_interactable"]("zone_a", {
        "id": "h_snap", "label": "s", "position": {"cell": {"x": 0, "y": 0}},
        "action": {"type": "examine", "text": "t"}})
    assert res["ok"] is True
    from maestro.map_builder import open_cells
    p = state.read_component("places")["places"]["zone_a"]
    cell = next(i for i in p["interactables"] if i["id"] == "h_snap")["position"]["cell"]
    assert (cell["x"], cell["y"]) in open_cells(p["tiles"])
    # feature form works here too
    res = tools["add_interactable"]("zone_a", {
        "id": "h_feat", "label": "f", "position": {"feature": "x_east"},
        "action": {"type": "examine", "text": "t"}})
    assert res["ok"] is True
    res = tools["add_interactable"]("zone_a", {
        "id": "h_bad", "label": "b", "position": {"feature": "f_nope"},
        "action": {"type": "examine", "text": "t"}})
    assert res["ok"] is False and "f_nope" in res["error"]


def test_node_write_rejects_unknown_effect_types():
    from maestro.modules.scenes import effect_error
    assert effect_error({"set_flag": "x"}) is None
    assert effect_error({"remove_item": "item_k"}) is None
    err = effect_error({"type": "show_text", "text": "Verifying..."})
    assert err and "unknown effect" in err and "text" in err
