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


def test_node_view():
    v = c.node_view(_vn_artifact())
    assert v["node_ids"] == ["n1", "n2", "n3"]
    assert v["edges"]["n1"] == ["n2", "n3"]
    assert v["line_counts"]["n1"] == 2 and not v["unreachable"]


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
