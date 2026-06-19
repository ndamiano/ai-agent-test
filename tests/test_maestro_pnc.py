"""Point-and-click genre: structure checks, stitch, baseline, tools, genre classifier."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from maestro.spec import Spec
from maestro.state import RunState
from maestro.tools import build_tools
from renpy import pnc_checks
from renpy._pnc_script import stitch_pnc
from renpy.spec_baseline import enforce_baseline
from renpy.component_schemas import validate_component
import maestro.spec_tools as spec_tools


PREMISE = {"central_question": "Can you escape the cell?",
           "characters": [{"id": "warden", "name": "Warden"}]}

MANIFEST = {"backgrounds": [{"id": "bg_cell"}, {"id": "bg_hall"}],
            "characters": [{"id": "warden"}],
            "items": [{"id": "item_key"}]}

ROOMS = {
    "start_room": "room_cell",
    "items": [{"id": "item_key", "name": "Rusty Key"}],
    "flags": ["escaped"],
    "goal": {"type": "flag", "id": "escaped"},
    "room_ids": ["room_cell", "room_hall"],
    "rooms": {
        "room_cell": {"bg": "bg_cell", "hotspots": [
            {"id": "h_bunk", "rect": [100, 100, 80, 80], "label": "Bunk",
             "logic": '"A hard bunk bolted to the wall."\nreturn'},
            {"id": "h_key", "rect": [300, 200, 60, 60], "label": "Key",
             "logic": '$ if "item_key" not in inventory: inventory.append("item_key")\n"You pocket the key."\nreturn'},
            {"id": "h_door", "rect": [500, 150, 100, 200], "label": "Door",
             "logic": "jump room_hall"},
        ]},
        "room_hall": {"bg": "bg_hall", "hotspots": [
            {"id": "h_gate", "rect": [400, 150, 120, 220], "label": "Gate",
             "logic": 'if "item_key" in inventory:\n    $ escaped = True\n    jump win\nelse:\n    "Locked. You need a key."\n    return'},
            {"id": "h_back", "rect": [50, 600, 80, 60], "label": "Back",
             "logic": "jump room_cell"},
        ]},
    },
}


def _artifact(**over):
    rooms = {**ROOMS, **over}
    return {"premise": PREMISE, "asset_manifest": MANIFEST, "rooms": rooms}


# ── structure checks ──────────────────────────────────────────────────────────
def test_all_checks_pass_on_a_well_formed_adventure():
    art = _artifact()
    for name, fn in pnc_checks._CHECKS.items():
        ok, detail = fn(art, {"min": 2}, None)
        assert ok, f"{name} should pass: {detail}"


def test_unreachable_room_fails_reachability():
    rooms = {**ROOMS, "room_ids": ["room_cell", "room_hall", "room_attic"],
             "rooms": {**ROOMS["rooms"],
                       "room_attic": {"bg": "bg_cell", "hotspots": [
                           {"id": "h_x", "rect": [0, 0, 10, 10], "label": "X", "logic": "return"}]}}}
    ok, detail = pnc_checks.check_rooms_reachable({"rooms": rooms}, {}, None)
    assert not ok and "room_attic" in detail


def test_item_never_taken_fails_obtainable():
    rooms = {**ROOMS, "items": [{"id": "item_key"}, {"id": "item_ghost"}]}
    ok, detail = pnc_checks.check_items_obtainable({"rooms": rooms}, {}, None)
    assert not ok and "item_ghost" in detail


def test_item_never_used_fails_used():
    rooms = {**ROOMS, "items": [{"id": "item_key"}, {"id": "item_unused"}]}
    # add a take for item_unused so it's obtainable but never checked
    rooms = {**rooms, "rooms": {**ROOMS["rooms"],
             "room_cell": {**ROOMS["rooms"]["room_cell"], "hotspots":
                ROOMS["rooms"]["room_cell"]["hotspots"] + [
                {"id": "h_u", "rect": [0, 0, 10, 10], "label": "U",
                 "logic": 'inventory.append("item_unused")\nreturn'}]}}}
    ok, detail = pnc_checks.check_items_used({"rooms": rooms}, {}, None)
    assert not ok and "item_unused" in detail


def test_goal_flag_never_set_fails():
    rooms = {**ROOMS, "goal": {"type": "flag", "id": "never_set"}}
    ok, detail = pnc_checks.check_goal_reachable({"rooms": rooms}, {}, None)
    assert not ok and "never_set" in detail


def test_hotspot_out_of_bounds_fails():
    rooms = {**ROOMS, "rooms": {**ROOMS["rooms"],
             "room_cell": {**ROOMS["rooms"]["room_cell"], "hotspots": [
                 {"id": "h_off", "rect": [1200, 100, 200, 100], "label": "Off",
                  "logic": "return"}]}}}
    ok, detail = pnc_checks.check_hotspots_in_bounds({"rooms": rooms}, {}, None)
    assert not ok and "h_off" in detail


def test_room_view_reports_graph_and_item_coverage():
    view = pnc_checks.room_view(_artifact())
    assert view["reachable"] == ["room_cell", "room_hall"]
    assert view["unreachable"] == []
    assert view["items_never_taken"] == [] and view["items_never_used"] == []
    assert "room_hall" in view["edges"]["room_cell"]


# ── stitch ────────────────────────────────────────────────────────────────────
def test_stitch_emits_screens_state_and_hotspot_labels():
    script, issues = stitch_pnc(PREMISE, MANIFEST, ROOMS, {}, {"warden", "act"})
    assert issues == {}
    assert "screen room_cell():" in script
    assert "screen inventory_bar():" in script
    assert "default inventory = []" in script
    assert "default escaped = False" in script
    assert "label start:\n    jump room_cell" in script
    assert "label hs_room_cell_h_key:" in script        # generated from the hotspot id
    assert 'action Call("hs_room_cell_h_key")' in script  # screen wiring matches the label
    assert "call screen room_cell" in script
    assert "label win:" in script


def test_stitch_surfaces_dangling_jump_as_issue():
    rooms = {**ROOMS, "rooms": {**ROOMS["rooms"],
             "room_cell": {**ROOMS["rooms"]["room_cell"], "hotspots": [
                 {"id": "h_door", "rect": [0, 0, 10, 10], "label": "Door",
                  "logic": "jump room_nowhere"}]}}}
    script, issues = stitch_pnc(PREMISE, MANIFEST, rooms, {}, {"warden", "act"})
    assert script == "" and "room_cell" in issues and "room_nowhere" in issues["room_cell"]


def test_hotspot_without_terminator_gets_a_return():
    rooms = {**ROOMS, "rooms": {"room_cell": {"bg": "bg_cell", "hotspots": [
        {"id": "h_x", "rect": [0, 0, 10, 10], "label": "X", "logic": '"just a line"'},
        {"id": "h_y", "rect": [0, 0, 10, 10], "label": "Y", "logic": "jump room_cell"}]}},
        "room_ids": ["room_cell"], "start_room": "room_cell", "items": [], "goal": None}
    script, _ = stitch_pnc(PREMISE, MANIFEST, rooms, {}, {"warden", "act"})
    block = script.split("label hs_room_cell_h_x:")[1].split("label ")[0]
    assert "return" in block


# ── baseline ──────────────────────────────────────────────────────────────────
def test_enforce_baseline_pnc_injects_room_floor():
    spec = {"genre": "point_and_click", "components": [
        {"id": "rooms", "done_conditions": []},
        {"id": "premise", "done_conditions": []}]}
    enforce_baseline(spec)
    rooms_checks = {c["type"] for c in spec["components"][0]["done_conditions"]}
    assert {"rooms_reachable", "items_obtainable", "goal_reachable",
            "hotspots_in_bounds", "compiles"} <= rooms_checks


def test_enforce_baseline_pnc_orders_rooms_after_dialogue():
    # rooms talk-hotspots call node_scripts dialogue, so rooms must depend on (build after) it.
    spec = {"genre": "point_and_click", "components": [
        {"id": "premise"}, {"id": "asset_manifest"}, {"id": "node_scripts"}, {"id": "rooms"}]}
    enforce_baseline(spec)
    rooms = next(c for c in spec["components"] if c["id"] == "rooms")
    assert set(rooms["deps"]) >= {"premise", "asset_manifest", "node_scripts"}
    order = Spec(spec).dep_order()
    assert order.index("node_scripts") < order.index("rooms")


def test_enforce_baseline_pnc_breaks_proposer_dep_cycle():
    # Proposer emitted node_scripts -> rooms; our rooms -> node_scripts would close a cycle.
    # Authoritative deps must overwrite it so dep_order resolves.
    spec = {"genre": "point_and_click", "components": [
        {"id": "premise", "deps": ["rooms"]},
        {"id": "asset_manifest", "deps": []},
        {"id": "node_scripts", "deps": ["rooms"]},
        {"id": "rooms", "deps": []}]}
    enforce_baseline(spec)
    order = Spec(spec).dep_order()   # must not raise a cycle
    assert order.index("node_scripts") < order.index("rooms")
    assert Spec(spec).data["components"][0]["deps"] == []   # premise's bogus dep stripped


def test_enforce_baseline_vn_unchanged_by_pnc():
    spec = {"genre": "vn", "components": [{"id": "node_scripts", "done_conditions": []}]}
    enforce_baseline(spec)
    types = {c["type"] for c in spec["components"][0]["done_conditions"]}
    assert "reachable_from_start" in types and "rooms_reachable" not in types


# ── schema ────────────────────────────────────────────────────────────────────
def test_rooms_schema_accepts_valid_and_rejects_bad_rect():
    assert validate_component("rooms", ROOMS) is None
    bad = {**ROOMS, "rooms": {"room_cell": {"bg": "bg_cell", "hotspots": [
        {"id": "h", "rect": [1, 2, 3], "label": "L", "logic": "return"}]}},
        "room_ids": ["room_cell"]}
    assert "rect" in validate_component("rooms", bad)


# ── tools ─────────────────────────────────────────────────────────────────────
def _pnc_spec():
    return Spec({"title": "Esc", "genre": "point_and_click", "frozen": True, "components": [
        {"id": "rooms", "deps": [], "done_conditions": [{"type": "compiles"}]}]})


def test_write_room_persists_and_registers_room_id(tmp_path):
    tools = build_tools(_pnc_spec(), RunState(tmp_path))
    res = tools["write_room"]("room_cell", ROOMS["rooms"]["room_cell"])
    assert res["ok"] is True
    rooms = RunState(tmp_path).read_component("rooms")
    assert "room_cell" in rooms["rooms"] and rooms["room_ids"] == ["room_cell"]
    assert rooms["start_room"] == "room_cell"


def test_write_room_accepts_use_puzzle_with_if_block(tmp_path):
    # `if "item" in inventory:` must not be misread as a dialogue speaker named "if".
    tools = build_tools(_pnc_spec(), RunState(tmp_path))
    res = tools["write_room"]("room_hall", ROOMS["rooms"]["room_hall"])
    assert res["ok"] is True


def test_write_component_authors_whole_rooms_and_normalizes_logic(tmp_path):
    # The model's natural output: the entire rooms component in one call, with over-escaped
    # logic. write_component must accept it and fix the escaping.
    from maestro.tools import build_tools
    from renpy.component_schemas import SCHEMAS
    state = RunState(tmp_path)
    tools = build_tools(_pnc_spec(), state, schemas=SCHEMAS)
    whole = {**ROOMS, "rooms": {"room_cell": {"bg": "bg_cell", "hotspots": [
        {"id": "h", "rect": [0, 0, 10, 10], "label": "X",
         "logic": 'label x:\\n    \\"hi\\"\\n    return'}]}}, "room_ids": ["room_cell"]}
    res = tools["write_component"]("rooms", whole)
    assert res["ok"] is True
    logic = state.read_component("rooms")["rooms"]["room_cell"]["hotspots"][0]["logic"]
    assert "\\n" not in logic and "\n" in logic        # over-escaping collapsed to real newlines


def test_write_room_rejects_missing_bg(tmp_path):
    tools = build_tools(_pnc_spec(), RunState(tmp_path))
    res = tools["write_room"]("room_x", {"hotspots": [
        {"id": "h", "rect": [0, 0, 1, 1], "label": "L", "logic": "return"}]})
    assert res["ok"] is False and "bg" in res["error"]


def test_set_rooms_meta_declares_goal_and_merges(tmp_path):
    state = RunState(tmp_path)
    tools = build_tools(_pnc_spec(), state)
    tools["write_room"]("room_cell", ROOMS["rooms"]["room_cell"])
    res = tools["set_rooms_meta"](goal={"type": "flag", "id": "escaped"},
                                  items=[{"id": "item_key"}], flags=["escaped"])
    assert res["ok"] is True
    rooms = state.read_component("rooms")
    assert rooms["goal"] == {"type": "flag", "id": "escaped"}
    assert rooms["flags"] == ["escaped"]
    assert "room_cell" in rooms["rooms"]      # write_room's work survived the merge


def test_set_rooms_meta_rejects_bad_goal(tmp_path):
    tools = build_tools(_pnc_spec(), RunState(tmp_path))
    assert tools["set_rooms_meta"](goal={"id": "x"})["ok"] is False        # missing type
    assert tools["set_rooms_meta"](goal={"type": "weird", "id": "x"})["ok"] is False


def test_build_backfills_icons_for_undeclared_picked_up_items():
    from renpy.fns import _merge_items_into_manifest
    rooms = {"rooms": {"room_a": {"hotspots": [
        {"id": "h", "logic": 'inventory.append("item_ghost")\nreturn'}]}}, "items": []}
    merged = _merge_items_into_manifest(rooms, {"items": []})
    assert any(i["id"] == "item_ghost" for i in merged["items"])


def test_edit_room_patches_one_hotspot(tmp_path):
    state = RunState(tmp_path)
    tools = build_tools(_pnc_spec(), state)
    tools["write_room"]("room_cell", ROOMS["rooms"]["room_cell"])
    res = tools["edit_room"]("room_cell", "h_door", "room_hall", "room_attic")
    assert res["ok"] is True
    hs = [h for h in state.read_component("rooms")["rooms"]["room_cell"]["hotspots"]
          if h["id"] == "h_door"][0]
    assert "jump room_attic" in hs["logic"]


# ── genre classifier ──────────────────────────────────────────────────────────
def test_classify_genre_keyword_path_skips_llm(monkeypatch):
    import llm_clients.inference as inf
    monkeypatch.setattr(inf, "json_with_correction",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("LLM called")))
    assert spec_tools._classify_genre("make me a point-and-click escape room") == "point_and_click"


def test_classify_genre_falls_back_to_llm_then_vn(monkeypatch):
    import llm_clients.inference as inf
    monkeypatch.setattr(inf, "json_with_correction", lambda *a, **k: {"genre": "point_and_click"})
    assert spec_tools._classify_genre("a game about a haunted house") == "point_and_click"
    monkeypatch.setattr(inf, "json_with_correction", lambda *a, **k: {"genre": "vn"})
    assert spec_tools._classify_genre("a quiet story about two friends") == "vn"


def test_classify_genre_llm_error_defaults_vn(monkeypatch):
    import llm_clients.inference as inf
    monkeypatch.setattr(inf, "json_with_correction",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down")))
    assert spec_tools._classify_genre("an ambiguous thing") == "vn"
