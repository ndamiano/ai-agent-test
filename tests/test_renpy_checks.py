import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from renpy.checks import (
    check_reachable_from_start, check_min_branches,
    check_each_node_min_lines, check_all_characters_speak, register_all,
)
from maestro.validate import validate
from maestro.spec import Spec
from maestro.state import RunState


def _art(scripts, node_ids=None, chars=None):
    return {
        "node_scripts": {"node_ids": node_ids or list(scripts), "scripts": scripts},
        "premise": {"characters": [{"id": c} for c in (chars or [])]},
    }


# ── reachable_from_start ─────────────────────────────────────────────────────

def test_reachable_all_nodes():
    art = _art({
        "scene_01": "label scene_01:\n    menu:\n        \"a\":\n            jump scene_02\n        \"b\":\n            jump ending_bad",
        "scene_02": "label scene_02:\n    jump ending_good",
        "ending_good": "label ending_good:\n    return",
        "ending_bad": "label ending_bad:\n    return",
    }, node_ids=["scene_01", "scene_02", "ending_good", "ending_bad"])
    assert check_reachable_from_start(art, {}, None) == (True, None)


def test_reachable_flags_orphan():
    art = _art({
        "scene_01": "label scene_01:\n    jump ending_good",
        "ending_good": "label ending_good:\n    return",
        "orphan": "label orphan:\n    return",   # never jumped to
    }, node_ids=["scene_01", "ending_good", "orphan"])
    ok, detail = check_reachable_from_start(art, {}, None)
    assert ok is False and "orphan" in detail


# ── min_branches ─────────────────────────────────────────────────────────────

def test_min_branches():
    art = _art({"s1": "label s1:\n    menu:\n        \"x\":\n            jump s2"})
    assert check_min_branches(art, {"min": 1}, None)[0] is True
    ok, detail = check_min_branches(art, {"min": 2}, None)
    assert ok is False and "need 2" in detail


# ── each_node_min_lines ──────────────────────────────────────────────────────

def test_each_node_min_lines():
    art = _art({
        "rich": 'label rich:\n    a "one"\n    b "two"\n    "narration"',
        "thin": 'label thin:\n    jump rich',
    })
    ok, detail = check_each_node_min_lines(art, {"min": 2}, None)
    assert ok is False and "thin" in detail
    assert check_each_node_min_lines(_art({"rich": art["node_scripts"]["scripts"]["rich"]}), {"min": 3}, None)[0] is True


# ── all_characters_speak ─────────────────────────────────────────────────────

def test_all_characters_speak():
    scripts = {"s1": 'label s1:\n    evelyn "hi"\n    jack "hey"'}
    assert check_all_characters_speak(_art(scripts, chars=["evelyn", "jack"]), {}, None) == (True, None)
    ok, detail = check_all_characters_speak(_art(scripts, chars=["evelyn", "jack", "lila"]), {}, None)
    assert ok is False and "lila" in detail


# ── register_all wires them into validate ────────────────────────────────────

def test_register_makes_checks_available_to_validate(tmp_path):
    register_all()
    state = RunState(tmp_path)
    state.write_component("node_scripts", {
        "node_ids": ["s1", "ending"], "scripts": {
            "s1": "label s1:\n    jump ending", "ending": "label ending:\n    return"}})
    spec = Spec({"frozen": True, "components": [
        {"id": "node_scripts", "done_conditions": [{"type": "reachable_from_start"}]}]})
    assert validate(spec, state) == []   # custom check ran, passed

    # add an orphan → the registered check now reports a failure through validate
    state.write_component("node_scripts", {
        "node_ids": ["s1", "ending", "orphan"], "scripts": {
            "s1": "label s1:\n    jump ending", "ending": "label ending:\n    return",
            "orphan": "label orphan:\n    return"}})
    failures = validate(spec, state)
    assert failures and failures[0]["check"]["type"] == "reachable_from_start"
