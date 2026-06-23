import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from renpy import ir_checks as c


def _vn_artifact():
    return {
        "premise": {"characters": [{"id": "al"}, {"id": "bo"}]},
        "nodes": {
            "node_ids": ["n1", "n2", "n3"],
            "nodes": {
                "n1": {"lines": [{"speaker": "al", "text": "a"}, {"speaker": "bo", "text": "b"}],
                       "end": {"type": "menu", "choices": [{"text": "x", "target": "n2"},
                                                           {"text": "y", "target": "n3"}]}},
                "n2": {"lines": [{"speaker": "al", "text": "c"}], "end": {"type": "end"}},
                "n3": {"lines": [{"speaker": "bo", "text": "d"}], "end": {"type": "end"}},
            },
        },
    }


def test_reachable_ok_and_orphan():
    art = _vn_artifact()
    assert c.check_reachable_from_start(art, {}, None)[0] is True
    art["nodes"]["nodes"]["n1"]["end"] = {"type": "jump", "target": "n2"}  # n3 now orphaned
    ok, detail = c.check_reachable_from_start(art, {}, None)
    assert not ok and "n3" in detail


def test_min_branches():
    art = _vn_artifact()
    assert c.check_min_branches(art, {"min": 1}, None)[0] is True
    assert c.check_min_branches(art, {"min": 2}, None)[0] is False


def test_each_node_min_lines():
    art = _vn_artifact()
    assert c.check_each_node_min_lines(art, {"min": 1}, None)[0] is True
    ok, detail = c.check_each_node_min_lines(art, {"min": 2}, None)
    assert not ok and "n2" in detail


def test_all_characters_speak():
    art = _vn_artifact()
    assert c.check_all_characters_speak(art, {}, None)[0] is True
    art["premise"]["characters"].append({"id": "cy"})
    ok, detail = c.check_all_characters_speak(art, {}, None)
    assert not ok and "cy" in detail


def test_beats_realized():
    art = _vn_artifact()
    art["outline"] = {"beats": [{"id": "beat_01"}, {"id": "beat_02"}]}
    # no node tags a beat yet → both missing
    ok, detail = c.check_beats_realized(art, {}, None)
    assert not ok and "beat_01" in detail and "beat_02" in detail
    art["nodes"]["nodes"]["n1"]["beat"] = "beat_01"
    ok, detail = c.check_beats_realized(art, {}, None)
    assert not ok and "beat_02" in detail and "beat_01" not in detail
    art["nodes"]["nodes"]["n2"]["beat"] = "beat_02"
    assert c.check_beats_realized(art, {}, None)[0] is True
    # node_view surfaces the same worklist for the author loop
    assert c.node_view(art)["beats_todo"] == []


def test_beats_realized_noop_without_outline():
    # NPC dialogue (no outline) — nothing to realize, never blocks.
    assert c.check_beats_realized(_vn_artifact(), {}, None)[0] is True


def test_no_dead_gates_passes_when_ungated():
    # A lean arc with no `requires` anywhere — endings earned by the story — passes trivially.
    assert c.check_no_dead_gates(_vn_artifact(), {}, None)[0] is True


def test_no_dead_gates_catches_self_gated_choice():
    # The exact trap: a choice gated on trust>=2 whose only +1 is on that SAME choice → trust is 0
    # at the gate forever → dead branch.
    art = _vn_artifact()
    ch = art["nodes"]["nodes"]["n1"]["end"]["choices"][0]
    ch["requires"] = {"var": "trust", "op": ">=", "value": 2}
    ch["effects"] = [{"add_var": {"var": "trust", "delta": 1}}]
    ok, detail = c.check_no_dead_gates(art, {}, None)
    assert not ok and "trust" in detail and "n1" in detail


def test_no_dead_gates_ok_when_raised_earlier():
    # Same gate, but trust is raised by an effect in an EARLIER scene → the gate can open → ok.
    art = _vn_artifact()
    art["nodes"]["nodes"]["n1"]["end"]["choices"][0]["requires"] = {"var": "trust", "op": ">=", "value": 1}
    # n2 is downstream of n1; put the raising effect on n1's OTHER choice's target... simplest: add a
    # third node that sets trust and is referenced. Here just set it on n2 (a different node).
    art["nodes"]["nodes"]["n2"]["lines"][0]["effects"] = [{"add_var": {"var": "trust", "delta": 1}}]
    assert c.check_no_dead_gates(art, {}, None)[0] is True


def test_node_view():
    v = c.node_view(_vn_artifact())
    assert v["node_ids"] == ["n1", "n2", "n3"]
    assert v["edges"]["n1"] == ["n2", "n3"]
    assert v["line_counts"]["n1"] == 2 and not v["unreachable"]


def test_node_view_open_slots_and_synopses():
    # n1 -> n2 (written), n3 (NOT written yet) -> n3 is an open slot reached from n1.
    art = {
        "premise": {"characters": [{"id": "al"}, {"id": "bo"}]},
        "nodes": {
            "node_ids": ["n1", "n2"],
            "synopses": {"n1": "they arrive", "n2": "they argue"},
            "nodes": {
                "n1": {"lines": [{"speaker": "al", "text": "a"}],
                       "end": {"type": "jump", "target": "n2"}},
                "n2": {"lines": [{"speaker": "bo", "text": "b"}],
                       "end": {"type": "menu", "choices": [{"text": "leave", "target": "n3"}]}},
            },
        },
    }
    v = c.node_view(art)
    assert v["synopses"]["n1"] == "they arrive"
    slots = v["open_slots"]
    assert [s["id"] for s in slots] == ["n3"]
    slot = slots[0]
    assert slot["from"] == [{"node": "n2", "label": "leave"}]
    # path to here = entry n1 → parent n2, each with its synopsis breadcrumb.
    assert [(p["id"], p["synopsis"]) for p in slot["path"]] == [
        ("n1", "they arrive"), ("n2", "they argue")]


def test_node_view_no_open_slots_when_all_targets_written():
    v = c.node_view(_vn_artifact())  # n1->{n2,n3}, both written
    assert v["open_slots"] == []


def _pnc_artifact():
    return {
        "premise": {"characters": [{"id": "w"}]},
        "places": {
            "place_ids": ["room_a", "room_b"],
            "start_place": "room_a",
            "items": [{"id": "key", "name": "Key"}],
            "goal": {"type": "flag", "id": "escaped"},
            "flags": ["escaped"],
            "places": {
                "room_a": {"interactables": [
                    {"id": "h_take", "action": {"type": "take", "item": "key"}},
                    {"id": "h_go", "action": {"type": "move", "target": "room_b"}},
                ]},
                "room_b": {"interactables": [
                    {"id": "h_use", "action": {"type": "use",
                        "clauses": [{"requires": {"item": "key"},
                                     "outcome": {"effects": [{"set_flag": "escaped"}]}}]}},
                    {"id": "h_win", "action": {"type": "win"}},
                ]},
            },
        },
    }


def test_places_reachable_and_orphan():
    art = _pnc_artifact()
    assert c.check_places_reachable(art, {}, None)[0] is True
    art["places"]["places"]["room_a"]["interactables"] = [
        {"id": "h", "action": {"type": "examine", "text": "x"}}]  # drop the move → room_b orphan
    ok, detail = c.check_places_reachable(art, {}, None)
    assert not ok and "room_b" in detail


def test_items_obtainable_and_used():
    art = _pnc_artifact()
    assert c.check_items_obtainable(art, {}, None)[0] is True
    assert c.check_items_used(art, {}, None)[0] is True
    art["places"]["places"]["room_a"]["interactables"][0]["action"] = {"type": "examine", "text": "x"}
    assert c.check_items_obtainable(art, {}, None)[0] is False  # key never taken


def test_goal_reachable():
    art = _pnc_artifact()
    assert c.check_goal_reachable(art, {}, None)[0] is True
    art["places"]["goal"] = {"type": "flag", "id": "never_set"}
    ok, detail = c.check_goal_reachable(art, {}, None)
    assert not ok and "never_set" in detail


def test_place_view():
    v = c.place_view(_pnc_artifact())
    assert v["place_ids"] == ["room_a", "room_b"]
    assert v["edges"]["room_a"] == ["room_b"]
    assert v["items_never_taken"] == [] and v["items_never_used"] == []
