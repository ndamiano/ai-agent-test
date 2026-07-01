import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from maestro.spec import Spec
from maestro.state import RunState
from maestro.tools import build_tools, SpecNotFrozen


def _spec(frozen=True):
    # Unconstrained: no modules => no structural schemas / locks, so these tests exercise the tool
    # mechanics (frozen gate, persistence, edits) without per-component validation getting in the way.
    return Spec({"title": "T", "frozen": frozen, "modules": [], "params": {}})


def _node(text="hi", end=None):
    return {"lines": [{"speaker": "a", "text": text}], "end": end or {"type": "return"}}


# ── add_interactable: append a hotspot without clobbering siblings ────────────

def _places_spec():
    return Spec({"title": "T", "frozen": True, "modules": [], "params": {}})


def _seed_place(state):
    state.write_component("places", {"place_ids": ["r1"], "places": {"r1": {
        "kind": "room", "background": "bg",
        "interactables": [{"id": "h_a", "action": {"type": "examine", "text": "x"}}]}}})


def test_add_interactable_appends_preserving_siblings(tmp_path):
    state = RunState(tmp_path)
    _seed_place(state)
    tools = build_tools(_places_spec(), state)
    res = tools["add_interactable"]("r1", {"id": "h_to_r2", "label": "exit",
        "position": {"rect": {"x": 1, "y": 1, "w": 1, "h": 1}},
        "action": {"type": "move", "target": "r2"}})
    assert res["ok"] is True
    ids = [i["id"] for i in state.read_component("places")["places"]["r1"]["interactables"]]
    assert ids == ["h_a", "h_to_r2"]                      # sibling preserved, new one appended


def test_add_interactable_rejects_dupe_and_bad_shape(tmp_path):
    state = RunState(tmp_path)
    _seed_place(state)
    tools = build_tools(_places_spec(), state)
    assert tools["add_interactable"]("r1", {"id": "h_a", "action": {"type": "examine", "text": "y"}})["ok"] is False
    assert tools["add_interactable"]("nope", {"id": "h_x", "action": {"type": "examine", "text": "y"}})["ok"] is False
    assert tools["add_interactable"]("r1", {"id": "h_y"})["ok"] is False   # action missing


# ── write-time action validation: catch malformed IR at the tool call ────────

def test_action_validation_rejects_empty_requires_with_hint():
    from maestro.tools import _action_struct_error
    err = _action_struct_error({"type": "use", "clauses": [
        {"requires": {}, "outcome": {"text": "pray", "effects": [{"set_flag": "f"}]}}]})
    assert err and "requires" in err and "fallback" in err   # actionable: use fallback instead


def test_action_validation_allows_unconditional_fallback_use():
    from maestro.tools import _action_struct_error
    # The unconditional pattern the model wanted (always set a flag) is fallback-only, no clauses.
    assert _action_struct_error({"type": "use",
        "fallback": {"text": "pray", "effects": [{"set_flag": "f"}]}}) is None
    assert _action_struct_error({"type": "use"}) is not None   # neither clauses nor fallback


def test_add_interactable_rejects_invalid_action(tmp_path):
    state = RunState(tmp_path)
    _seed_place(state)
    tools = build_tools(_places_spec(), state)
    bad = {"id": "h_bad", "position": {"rect": {"x": 1, "y": 1, "w": 1, "h": 1}},
           "action": {"type": "use", "clauses": [{"requires": {}, "outcome": {"text": "x"}}]}}
    res = tools["add_interactable"]("r1", bad)
    assert res["ok"] is False and "requires" in res["error"]


# ── generic component / state tools ──────────────────────────────────────────

def test_write_component_refuses_when_unfrozen(tmp_path):
    tools = build_tools(_spec(frozen=False), RunState(tmp_path))
    with pytest.raises(SpecNotFrozen):
        tools["write_component"]("premise", {"x": 1})


def test_write_and_read_component(tmp_path):
    tools = build_tools(_spec(), RunState(tmp_path))
    assert tools["write_component"]("premise", {"central_question": "Q?"})["ok"] is True
    got = tools["read_component"]("premise")
    assert got["ok"] is True and got["content"]["central_question"] == "Q?"
    assert tools["read_component"]("missing")["ok"] is False


def test_validate_tool_reports_failures(tmp_path):
    from maestro.modules import compose
    spec = {"frozen": True, "modules": ["cast"], "params": {}}
    tools = build_tools(spec, RunState(tmp_path), compose(("cast",)))
    assert tools["validate"]()["ok"] is False        # empty cast -> cast reports errors
    tools["write_component"]("characters",
                             {"characters": [{"id": "a", "name": "A"}, {"id": "b", "name": "B"}]})
    assert tools["validate"]()["ok"] is True


def test_no_manual_compile_tool(tmp_path):
    # The executor runs `compiles` every step; a manual compile tool only wastes steps and lets
    # the agent compile early, fighting the "compiles last" ordering — so it must not exist.
    tools = build_tools(_spec(), RunState(tmp_path))
    assert "compile_renpy" not in tools and "compile" not in tools


def test_generate_asset_wraps_image_gen(tmp_path, monkeypatch):
    import renpy.fns as fns
    monkeypatch.setattr(fns, "generate_images", lambda inputs, wd: {"status": "ok", "generated": ["bg_x.png"]})
    tools = build_tools(_spec(), RunState(tmp_path))
    res = tools["generate_asset"]()
    assert res["ok"] is True and res["generated"] == ["bg_x.png"]


def test_generate_asset_refuses_when_unfrozen(tmp_path):
    tools = build_tools(_spec(frozen=False), RunState(tmp_path))
    with pytest.raises(SpecNotFrozen):
        tools["generate_asset"]()


def test_update_scratchpad(tmp_path):
    state = RunState(tmp_path)
    tools = build_tools(_spec(), state)
    tools["update_scratchpad"](current_goal="write premise", open_questions=["which tone?"])
    pad = state.read_scratchpad()
    assert pad["current_goal"] == "write premise"
    assert pad["open_questions"] == ["which tone?"]


def test_request_review_returns_pending(tmp_path):
    tools = build_tools(_spec(), RunState(tmp_path))
    res = tools["request_review"]("Romance or tragedy?", ["romance", "tragedy"])
    assert res["status"] == "review_requested" and res["options"] == ["romance", "tragedy"]


# ── structured node tools ────────────────────────────────────────────────────

def test_write_node_persists_ir_object(tmp_path):
    state = RunState(tmp_path)
    tools = build_tools(_spec(), state)
    res = tools["write_node"]("scene_01", _node("hello", {"type": "jump", "target": "scene_02"}))
    assert res["ok"] is True
    ns = state.read_component("nodes")
    assert ns["node_ids"] == ["scene_01"]
    assert ns["nodes"]["scene_01"]["lines"][0]["text"] == "hello"
    assert ns["nodes"]["scene_01"]["end"] == {"type": "jump", "target": "scene_02"}


def test_write_node_stamps_system_beat(tmp_path):
    state = RunState(tmp_path)
    tools = build_tools(_spec(), state)
    tools["write_node"]("scene_01", _node("hi", {"type": "jump", "target": "scene_02"}),
                        beat="beat_03")
    assert state.read_component("nodes")["nodes"]["scene_01"]["beat"] == "beat_03"


def test_write_node_rejects_bad_shape(tmp_path):
    tools = build_tools(_spec(), RunState(tmp_path))
    assert tools["write_node"]("s1", "label s1:\n  a \"hi\"")["ok"] is False   # string, not object
    assert tools["write_node"]("s1", {"lines": [], "end": {"type": "return"}})["ok"] is False  # empty
    assert tools["write_node"]("s1", {"lines": [{"text": "x"}]})["ok"] is False  # no end
    assert tools["write_node"]("s1", {"lines": [{"text": "x"}], "end": {"type": "boom"}})["ok"] is False


def test_write_node_rejects_start_id(tmp_path):
    tools = build_tools(_spec(), RunState(tmp_path))
    assert tools["write_node"]("start", _node())["ok"] is False


def test_write_node_merges_story_state_delta(tmp_path):
    state = RunState(tmp_path)
    tools = build_tools(_spec(), state)
    tools["write_node"]("scene_01", _node(), new_facts=["a clue surfaced"],
                        entity_updates={"mara": {"trust": "wary"}})
    ss = state.read_story_state()
    assert "a clue surfaced" in ss["established_facts"]
    assert ss["entity_states"]["mara"]["trust"] == "wary"


def test_write_node_persists_event_summary_as_synopsis(tmp_path):
    state = RunState(tmp_path)
    tools = build_tools(_spec(), state)
    tools["write_node"]("scene_01", _node(), event_summary="sisters find the ledger")
    assert state.read_component("nodes")["synopses"]["scene_01"] == "sisters find the ledger"


def test_write_node_tolerates_malformed_delta(tmp_path):
    tools = build_tools(_spec(), RunState(tmp_path))
    assert tools["write_node"]("s1", _node(), story_state_delta=["new_facts"])["ok"] is True
    assert tools["write_node"]("s2", _node(), story_state_delta="oops")["ok"] is True


def test_edit_node_patches_line_and_end(tmp_path):
    state = RunState(tmp_path)
    tools = build_tools(_spec(), state)
    tools["write_node"]("s1", {"lines": [{"speaker": "a", "text": "old"}],
                               "end": {"type": "jump", "target": "s2"}})
    assert tools["edit_node"]("s1", line_index=0, text="new", speaker="b")["ok"] is True
    assert tools["edit_node"]("s1", end={"type": "return"})["ok"] is True
    node = state.read_component("nodes")["nodes"]["s1"]
    assert node["lines"][0] == {"speaker": "b", "text": "new"}
    assert node["end"] == {"type": "return"}


def test_edit_node_errors(tmp_path):
    state = RunState(tmp_path)
    tools = build_tools(_spec(), state)
    tools["write_node"]("s1", _node())
    assert tools["edit_node"]("missing", end={"type": "return"})["ok"] is False
    assert tools["edit_node"]("s1", line_index=9, text="x")["ok"] is False
    assert tools["edit_node"]("s1", end={"type": "boom"})["ok"] is False


def test_write_node_enforces_min_lines_floor(tmp_path):
    # When the spec demands each_node_min_lines, a thin node is rejected at write time
    # (born-compliant) so the loop never enters a separate repair phase for it.
    spec = Spec({"title": "T", "frozen": True, "modules": [],
                 "params": {"each_node_min_lines": 3}})
    tools = build_tools(spec, RunState(tmp_path))
    thin = {"lines": [{"speaker": "a", "text": "hi"}], "end": {"type": "return"}}
    res = tools["write_node"]("s1", thin)
    assert res["ok"] is False and "at least 3" in res["error"]
    fat = {"lines": [{"text": "a"}, {"text": "b"}, {"text": "c"}], "end": {"type": "return"}}
    assert tools["write_node"]("s1", fat)["ok"] is True


def test_write_node_caps_menu_width(tmp_path):
    # A wide menu is the hub-and-spoke star that guts the arc: rejected at write time so the
    # slot-driven loop can't hit its node quota by fanning one node into many stub branches.
    tools = build_tools(_spec(), RunState(tmp_path))
    wide = {"lines": [{"speaker": "a", "text": "go where?"}],
            "end": {"type": "menu", "choices": [
                {"text": f"room {i}", "target": f"scene_{i}"} for i in range(5)]}}
    res = tools["write_node"]("hub", wide)
    assert res["ok"] is False and "dramatic fork" in res["error"].lower()

    ok = {"lines": [{"speaker": "a", "text": "go where?"}],
          "end": {"type": "menu", "choices": [
              {"text": "open it", "target": "ending_truth"},
              {"text": "leave it", "target": "ending_silence"}]}}
    assert tools["write_node"]("fork", ok)["ok"] is True
    # linear flow is unaffected — a jump can lead anywhere, no width limit applies.
    assert tools["write_node"]("s1", _node(end={"type": "jump", "target": "s2"}))["ok"] is True


def test_write_node_rejects_all_gated_menu(tmp_path):
    # A menu where every choice is gated by `requires` can come up empty at runtime → dead-end.
    # Require at least one unconditional fallback.
    tools = build_tools(_spec(), RunState(tmp_path))
    all_gated = {"lines": [{"speaker": "a", "text": "the crisis"}],
                 "end": {"type": "menu", "choices": [
                     {"text": "earned", "target": "ending_truth",
                      "requires": {"var": "trust", "op": ">=", "value": 2}},
                     {"text": "also gated", "target": "ending_silence",
                      "requires": {"var": "trust", "op": "<", "value": 0}}]}}
    res = tools["write_node"]("crisis", all_gated)
    assert res["ok"] is False and "fallback" in res["error"].lower()

    with_fallback = {"lines": [{"speaker": "a", "text": "the crisis"}],
                     "end": {"type": "menu", "choices": [
                         {"text": "earned", "target": "ending_truth",
                          "requires": {"var": "trust", "op": ">=", "value": 2}},
                         {"text": "fallback", "target": "ending_silence"}]}}
    assert tools["write_node"]("crisis", with_fallback)["ok"] is True


def test_write_node_rejects_fake_fork(tmp_path):
    # A menu whose choices all lead to the same scene is illusory agency — rejected.
    tools = build_tools(_spec(), RunState(tmp_path))
    fake = {"lines": [{"speaker": "a", "text": "pick one"}],
            "end": {"type": "menu", "choices": [
                {"text": "left", "target": "scene_02"},
                {"text": "right", "target": "scene_02"}]}}
    res = tools["write_node"]("fork", fake)
    assert res["ok"] is False and "same scene" in res["error"].lower()

    real = {"lines": [{"speaker": "a", "text": "pick one"}],
            "end": {"type": "menu", "choices": [
                {"text": "left", "target": "scene_02"},
                {"text": "right", "target": "scene_03"}]}}
    assert tools["write_node"]("fork", real)["ok"] is True


def test_read_node_returns_object(tmp_path):
    state = RunState(tmp_path)
    tools = build_tools(_spec(), state)
    tools["write_node"]("s1", _node("yo"))
    got = tools["read_node"]("s1")
    assert got["ok"] is True and got["content"]["lines"][0]["text"] == "yo"
    assert tools["read_node"]("missing")["ok"] is False


# ── structured place tools ───────────────────────────────────────────────────

def _place():
    return {"kind": "room", "background": "bg_x", "interactables": [
        {"id": "hs_door", "label": "Door", "position": {"rect": {"x": 0, "y": 0, "w": 10, "h": 10}},
         "action": {"type": "move", "target": "room_b"}}]}


def test_write_and_read_place(tmp_path):
    state = RunState(tmp_path)
    tools = build_tools(_spec(), state)
    assert tools["write_place"]("room_a", _place())["ok"] is True
    pl = state.read_component("places")
    assert pl["place_ids"] == ["room_a"] and pl["start_place"] == "room_a"
    assert tools["read_place"]("room_a")["content"]["background"] == "bg_x"
    assert tools["write_place"]("room_b", {"interactables": []})["ok"] is False  # empty


def test_write_component_rejects_place_ids_without_entries(tmp_path):
    # A places component listing place_ids with no matching `places` entry is internally
    # inconsistent: the create-guard sees the id (already-exists) while add_interactable/
    # edit_place can't find it (no place) — an unfixable stall. Reject it at write time.
    from maestro.modules import compose
    spec = {"frozen": True, "modules": ["world"], "params": {}}
    tools = build_tools(spec, RunState(tmp_path), compose(("world",)))
    res = tools["write_component"]("places", {"place_ids": ["room_a", "room_b"], "places": {}})
    assert res["ok"] is False and "no entry in places.places" in res["error"]
    # The reverse mismatch (entry not listed in place_ids) is rejected too.
    res = tools["write_component"]("places",
                                   {"place_ids": ["room_a"], "places": {"room_a": _place(),
                                                                        "room_b": _place()}})
    assert res["ok"] is False and "not listed in place_ids" in res["error"]
    # A consistent component passes.
    assert tools["write_component"]("places",
                                    {"place_ids": ["room_a"], "places": {"room_a": _place()}})["ok"]


def test_set_places_meta_and_edit_place(tmp_path):
    state = RunState(tmp_path)
    tools = build_tools(_spec(), state)
    tools["write_place"]("room_a", _place())
    tools["set_places_meta"](goal={"type": "flag", "id": "escaped"}, flags=["escaped"])
    pl = state.read_component("places")
    assert pl["goal"] == {"type": "flag", "id": "escaped"} and pl["flags"] == ["escaped"]
    assert tools["set_places_meta"](items=[{"id": "key"}])["ok"] is False  # items live in `items` now
    # repoint the move target
    assert tools["edit_place"]("room_a", "hs_door",
                               action={"type": "move", "target": "room_c"})["ok"] is True
    h = state.read_component("places")["places"]["room_a"]["interactables"][0]
    assert h["action"]["target"] == "room_c"
    assert tools["set_places_meta"](goal={"type": "x"})["ok"] is False  # bad goal shape


# ── locking ──────────────────────────────────────────────────────────────────

def _dep_spec():
    # cast's `characters` locks once complete; the scenes terminal owns `nodes`
    # (emits_compile → never locks, stays writable to the end).
    return {"title": "T", "frozen": True, "modules": ["cast", "scenes"], "params": {}}


def _dep_modules():
    from maestro.modules import compose
    return compose(("cast", "scenes"))


_FULL_CAST = {"characters": [{"id": "a", "name": "A"}, {"id": "b", "name": "B"}]}


def test_passing_component_locks_against_rewrite(tmp_path):
    state = RunState(tmp_path)
    tools = build_tools(_dep_spec(), state, _dep_modules())
    assert tools["write_component"]("characters", _FULL_CAST)["ok"]   # now satisfies cast
    res = tools["write_component"]("characters", {"characters": [{"id": "a", "name": "A"}]})
    assert res["ok"] is False and "locked" in res["error"]
    assert len(state.read_component("characters")["characters"]) == 2


def test_state_wiring_error_on_component_keeps_it_writable(tmp_path):
    # items passes its OWN checks but a state_wiring error lands on it ("cut the declaration") —
    # the lock must not refuse the very fix the error asks for.
    from maestro.modules import compose
    spec = {"title": "T", "frozen": True, "modules": ["cast", "world", "inventory", "state"],
            "params": {}}
    modules = compose(("cast", "world", "inventory", "state"))
    state = RunState(tmp_path)
    tools = build_tools(spec, state, modules)
    assert tools["write_component"]("items", {"items": [
        {"id": "item_orphan", "name": "Orphan"}]})["ok"]   # satisfies inventory's checks
    # state_wiring: item_orphan declared, never produced/consumed → error hosted on items
    res = tools["write_component"]("items", {"items": [
        {"id": "item_orphan2", "name": "Other"}]})
    assert res["ok"] is True   # cut/rewrite allowed while the wiring error is open


def test_human_todo_on_component_keeps_it_writable(tmp_path):
    from maestro.modules import human
    state = RunState(tmp_path)
    tools = build_tools(_dep_spec(), state, _dep_modules())
    assert tools["write_component"]("characters", _FULL_CAST)["ok"]   # locks
    human.add_todo(state, "characters", "make the villain meaner")
    res = tools["write_component"]("characters", _FULL_CAST)
    assert res["ok"] is True   # open human note targets it → writable


def test_leaf_component_never_locks(tmp_path):
    state = RunState(tmp_path)
    tools = build_tools(_dep_spec(), state, _dep_modules())
    # nodes is the compile terminal (emits_compile) → never locks, stays writable.
    n = {"location": "bg", "lines": [{"speaker": "a", "text": "x"}], "end": {"type": "return"}}
    assert tools["write_node"]("s1", n)["ok"]
    assert tools["write_node"]("s2", n)["ok"]
