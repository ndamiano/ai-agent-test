"""Walkable RPG authoring: the `world` module emits/validates tile maps (tiles.legend/rows + cell
spawns) when `combat` is in the module set, and point-and-click rooms otherwise. Covers the spatial
validator (the loop's gate against a small model's bad layout), the return-trip connectivity gate,
the tool plumbing (set_places_meta start_spawn), the assemble lift (start.spawn + genre), and the
context-driven style selection."""
import copy
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from conftest import make_spec
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
# Each row is one defect the layout gate must catch (or a valid map it must pass): a small model
# produces exactly these bad layouts, and a wrong/missing message sends it fixing the wrong thing.

def _missing_tiles():
    b = copy.deepcopy(_VALID)
    del b["places"]["z1"]["tiles"]
    return b


def _no_start_spawn():
    b = copy.deepcopy(_VALID)
    del b["start_spawn"]
    return b


def _corridor():
    # A 5x1 corridor: spawn (0,0), enemy (4,0), a wall at (2,0) — enemy is NOT on a wall but is
    # unreachable. This is the load-bearing reachability check (schema can't see it).
    return {
        "start_place": "z1", "start_spawn": {"cell": {"x": 0, "y": 0}},
        "place_ids": ["z1"],
        "places": {"z1": {"kind": "world_map", "tiles": _tiles(["..#.."]),
            "interactables": [
                {"id": "enemy", "position": {"cell": {"x": 4, "y": 0}},
                 "action": {"type": "examine", "text": "t"}}]}}}


def _move_no_spawn():
    two = copy.deepcopy(_VALID)
    two["place_ids"].append("z2")
    two["places"]["z2"] = {"kind": "interior", "tiles": _tiles(["...", "...", "..."]),
        "interactables": [{"id": "back", "position": {"cell": {"x": 0, "y": 0}},
                           "action": {"type": "examine", "text": "t"}}]}
    # a move from z1 -> z2 (walkable) WITHOUT a spawn must be rejected
    two["places"]["z1"]["interactables"].append(
        {"id": "door", "position": {"cell": {"x": 0, "y": 0}},
         "action": {"type": "move", "target": "z2"}})
    return two


def _bad_arrival_spawn():
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
    return two


def _pnc_room():
    return {"places": {"r": {"kind": "room", "interactables": [
        {"id": "h", "position": {"rect": {"x": 1, "y": 1, "w": 9, "h": 9}},
         "action": {"type": "examine", "text": "t"}}]}}}


@pytest.mark.parametrize("artifact, expect", [
    (_VALID, None),
    # custom legend char is honoured, not flagged as unknown
    (_z1(tiles={"legend": {"W": {"role": "blocked", "theme": "hedge"}},
                "rows": ["..W.", "....", "..W."]}), None),
    # a point-and-click room (rect hotspots) is not subject to the walkable-grid checks
    (_pnc_room(), None),
    (_z1(interactables=[{"id": "x", "position": {"cell": {"x": 9, "y": 1}},
                         "action": {"type": "examine", "text": "t"}}]),
     ["outside the 4x3 grid"]),
    (_z1(interactables=[
        {"id": "a", "position": {"cell": {"x": 1, "y": 1}}, "action": {"type": "examine", "text": "t"}},
        {"id": "b", "position": {"cell": {"x": 1, "y": 1}}, "action": {"type": "examine", "text": "t"}}]),
     ["two interactables on tile"]),
    # re-paint so the enemy's tile (3,1) is a wall — an interactable can't sit on a blocked tile
    (_z1(tiles=_tiles(["..#.", "...#", "..#."])), ["on a blocked tile"]),
    (_z1(tiles=_tiles(["....", "..", "...."])), ["ragged"]),
    (_z1(tiles=_tiles(["..Q.", "....", "...."])), ["no legend entry"]),
    (_corridor(), ["walled off from the spawn"]),
    (_missing_tiles(), ["'tiles'"]),
    (_no_start_spawn(), ["walkable start place"]),
    (_move_no_spawn(), ["no 'spawn'"]),
    # two independent defects surface in ONE message — reporting them one at a time makes the model
    # ping-pong
    (_z1(interactables=[
        {"id": "oob", "position": {"cell": {"x": 9, "y": 1}},
         "action": {"type": "examine", "text": "t"}},
        {"id": "walled", "position": {"cell": {"x": 2, "y": 0}},
         "action": {"type": "examine", "text": "t"}}]),
     ["outside the 4x3 grid", "on a blocked tile"]),
    # the message names the SOURCE move ('h_to_z2' in 'z1'), not just the destination
    (_bad_arrival_spawn(), ["'h_to_z2'", "'z1'"]),
], ids=["valid", "custom_legend", "pnc_room_untouched", "out_of_bounds", "overlapping",
        "on_blocked_tile", "ragged_rows", "unknown_char", "walled_off", "missing_tiles",
        "start_needs_spawn", "move_needs_spawn", "all_issues_together", "bad_arrival_names_source"])
def test_rpg_world_error(artifact, expect):
    err = _rpg_world_error(artifact)
    if expect is None:
        assert err is None
    else:
        for frag in expect:
            assert frag in err


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


@pytest.mark.parametrize("moves_back, expect", [
    # a one-way trip strands the player in z2; a round trip is clean
    (False, ["no way back", "z2"]),
    (True, None),
], ids=["one_way_flagged", "round_trip_passes"])
def test_return_path(moves_back, expect):
    err = _return_path_error(_two_zone(z1_moves_back=moves_back))
    if expect is None:
        assert err is None
    else:
        for frag in expect:
            assert frag in err.lower()


# ── tool plumbing: set_places_meta start_spawn ────────────────────────────────

def test_set_places_meta_stores_start_spawn(tmp_path):
    state = RunState(tmp_path)
    tools = build_tools(make_spec(), state)
    res = tools["set_places_meta"](start_place="z1", start_spawn={"cell": {"x": 2, "y": 3}})
    assert res["ok"] and res["start_spawn"] == {"cell": {"x": 2, "y": 3}}
    assert state.read_component("places")["start_spawn"] == {"cell": {"x": 2, "y": 3}}


def test_set_places_meta_rejects_bad_spawn(tmp_path):
    tools = build_tools(make_spec(), RunState(tmp_path))
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


@pytest.mark.parametrize("modules, skel, prompt", [
    # combat in the set => walkable RPG skeleton + prompt; without it => point-and-click
    (["world", "scenes", "combat"], SKEL_RPG, "places_rpg_write.txt"),
    (["world", "scenes"], SKEL_PLACES, "places_write.txt"),
], ids=["combat_game_rpg", "non_combat_pnc"])
def test_author_style_selection(modules, skel, prompt):
    ctx = _Ctx(modules)
    author = WORLD._check_for("start_place")   # a style-dependent author check
    assert author.skeleton(ctx) is skel
    assert author.prompt(ctx) == prompt


@pytest.mark.parametrize("art, code", [
    # a combat game whose only zone walls its enemy off -> rpg_layout
    ({"places": {
        "start_place": "z1", "start_spawn": {"cell": {"x": 0, "y": 0}}, "place_ids": ["z1"],
        "places": {"z1": {"kind": "world_map", "tiles": _tiles(["..#.."]),
            "interactables": [
                {"id": "enemy", "position": {"cell": {"x": 4, "y": 0}},
                 "action": {"type": "start_combat", "encounter": "e"}},
                {"id": "sign", "position": {"cell": {"x": 1, "y": 0}},
                 "action": {"type": "examine", "text": "t"}}]}}}},
     "rpg_layout"),
    # a one-way multi-zone map -> rpg_connectivity
    ({"places": _two_zone(z1_moves_back=False)}, "rpg_connectivity"),
], ids=["broken_map_layout", "one_way_connectivity"])
def test_get_errors_emits_rpg_code(art, code):
    ctx = _Ctx(["world", "scenes", "combat"], artifact=art)
    ctx.spec["params"] = {"min_places": 1, "min_interactables": 1}
    codes = [e.code for e in WORLD.get_errors(ctx)]
    assert code in codes


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
