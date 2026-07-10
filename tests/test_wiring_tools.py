"""The surgical wiring micro-tools (add_effect / remove_effect / add_gate / remove_gate) —
append-or-refuse semantics so a wiring fix can never cannibalize another value's wiring."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from maestro.state import RunState
from maestro.tools import build_tools
from conftest import make_spec


def _tools(tmp_path):
    state = RunState(tmp_path)
    state.write_component("nodes", {"node_ids": ["n1", "n2"], "nodes": {
        "n1": {"lines": [{"speaker": "a", "text": "hi"},
                         {"speaker": None, "text": "x", "effects": [{"set_flag": "old"}]}],
               "end": {"type": "menu", "choices": [
                   {"text": "go", "target": "n2"},
                   {"text": "stay", "target": "n1", "requires": {"flag": "old"}}]}},
        "n2": {"lines": [{"speaker": "a", "text": "yo"}],
               "end": {"type": "menu", "choices": [
                   {"text": "a", "target": "n1"},
                   {"text": "b", "target": "n1"},
                   {"text": "c", "target": "n1", "requires": {"flag": "old"}}]}}}})
    state.write_component("places", {"place_ids": ["r1"], "places": {"r1": {
        "kind": "room", "background": "bg", "interactables": [
            {"id": "h_use", "action": {"type": "use", "fallback": {
                "text": "t", "effects": [{"set_flag": "a"}]}}},
            {"id": "h_use_bare", "action": {"type": "use", "clauses": [{
                "requires": {"item": "key"},
                "outcome": {"text": "o", "effects": [{"set_flag": "b"}]}}]}},
            {"id": "h_move", "action": {"type": "move", "target": "r1"}},
            {"id": "h_move_gated", "action": {"type": "move", "target": "r1",
                                              "requires": {"flag": "old"}}},
            {"id": "h_exam", "action": {"type": "examine", "text": "x"}}]}}})
    return state, build_tools(make_spec(), state)


# ── add_effect: append, never replace ──────────────────────────────────────────

def test_add_effect_appends_to_line_preserving_existing(tmp_path):
    state, tools = _tools(tmp_path)
    r = tools["add_effect"]({"set_flag": "new"}, node_id="n1", line_index=1)
    assert r["ok"] is True
    effs = state.read_component("nodes")["nodes"]["n1"]["lines"][1]["effects"]
    assert effs == [{"set_flag": "old"}, {"set_flag": "new"}]


def test_add_effect_appends_to_menu_choice(tmp_path):
    state, tools = _tools(tmp_path)
    r = tools["add_effect"]({"add_item": "coin"}, node_id="n1", choice_index=0)
    assert r["ok"] is True
    ch = state.read_component("nodes")["nodes"]["n1"]["end"]["choices"][0]
    assert ch["effects"] == [{"add_item": "coin"}]


def test_add_effect_refuses_duplicate_and_malformed(tmp_path):
    state, tools = _tools(tmp_path)
    r = tools["add_effect"]({"set_flag": "old"}, node_id="n1", line_index=1)
    assert r["ok"] is False and "already" in r["error"]
    # the exact malformed shape that crashed the live build's detectors
    r = tools["add_effect"]({"set_flag": {"flag": "x", "value": True}},
                            node_id="n1", line_index=1)
    assert r["ok"] is False
    r = tools["add_effect"]({"set_flag": "x"}, node_id="n1")           # no site index
    assert r["ok"] is False
    r = tools["add_effect"]({"set_flag": "x"}, node_id="n1", line_index=0, choice_index=0)
    assert r["ok"] is False


def test_add_effect_on_use_fallback_and_creation(tmp_path):
    state, tools = _tools(tmp_path)
    r = tools["add_effect"]({"set_flag": "c"}, place_id="r1", interactable_id="h_use")
    assert r["ok"] is True
    fb = _action(state, "h_use")["fallback"]
    assert fb["effects"] == [{"set_flag": "a"}, {"set_flag": "c"}]
    # no fallback yet: refuse without text, create with it
    r = tools["add_effect"]({"set_flag": "d"}, place_id="r1", interactable_id="h_use_bare")
    assert r["ok"] is False and "text" in r["error"]
    r = tools["add_effect"]({"set_flag": "d"}, place_id="r1", interactable_id="h_use_bare",
                            text="it clicks")
    assert r["ok"] is True
    fb = _action(state, "h_use_bare")["fallback"]
    assert fb == {"text": "it clicks", "effects": [{"set_flag": "d"}]}


def test_add_effect_refuses_non_use_action(tmp_path):
    state, tools = _tools(tmp_path)
    r = tools["add_effect"]({"set_flag": "x"}, place_id="r1", interactable_id="h_exam")
    assert r["ok"] is False and "add_interactable" in r["error"]


def _action(state, hid, pid="r1"):
    place = state.read_component("places")["places"][pid]
    return next(i for i in place["interactables"] if i["id"] == hid)["action"]


# ── remove_effect: exactly the one named effect ────────────────────────────────

def test_remove_effect_surgical(tmp_path):
    state, tools = _tools(tmp_path)
    tools["add_effect"]({"set_flag": "new"}, node_id="n1", line_index=1)
    r = tools["remove_effect"]({"set_flag": "old"}, node_id="n1", line_index=1)
    assert r["ok"] is True
    assert state.read_component("nodes")["nodes"]["n1"]["lines"][1]["effects"] \
        == [{"set_flag": "new"}]
    r = tools["remove_effect"]({"set_flag": "gone"}, node_id="n1", line_index=1)
    assert r["ok"] is False and "set_flag" in r["error"]              # names what IS there


def test_remove_effect_reaches_clause_outcomes(tmp_path):
    state, tools = _tools(tmp_path)
    r = tools["remove_effect"]({"set_flag": "b"}, place_id="r1", interactable_id="h_use_bare")
    assert r["ok"] is True
    assert _action(state, "h_use_bare")["clauses"][0]["outcome"]["effects"] == []


# ── add_gate: only an ungated site, never a dead menu ──────────────────────────

def test_add_gate_sets_ungated_choice_and_refuses_gated(tmp_path):
    state, tools = _tools(tmp_path)
    r = tools["add_gate"]({"flag": "old"}, node_id="n2", choice_index=0)
    assert r["ok"] is True
    assert state.read_component("nodes")["nodes"]["n2"]["end"]["choices"][0]["requires"] \
        == {"flag": "old"}
    r = tools["add_gate"]({"flag": "x"}, node_id="n2", choice_index=2)   # already gated
    assert r["ok"] is False and "another value's wiring" in r["error"]


def test_add_gate_refuses_last_open_choice(tmp_path):
    state, tools = _tools(tmp_path)
    # n1's menu: choice 0 open, choice 1 gated — gating 0 would dead-end the menu
    r = tools["add_gate"]({"flag": "x"}, node_id="n1", choice_index=0)
    assert r["ok"] is False and "always-open" in r["error"]


def test_add_gate_on_hotspot(tmp_path):
    state, tools = _tools(tmp_path)
    r = tools["add_gate"]({"item": "key"}, place_id="r1", interactable_id="h_move")
    assert r["ok"] is True
    assert _action(state, "h_move")["requires"] == {"item": "key"}
    r = tools["add_gate"]({"flag": "x"}, place_id="r1", interactable_id="h_move_gated")
    assert r["ok"] is False and "another value's wiring" in r["error"]
    r = tools["add_gate"]({"flag": "x"}, place_id="r1", interactable_id="h_exam")
    assert r["ok"] is False                                # an examine can't carry requires
    r = tools["add_gate"]({"flag": "x"}, place_id="r1", interactable_id="h_use")
    assert r["ok"] is False and "clauses" in r["error"]


def test_add_gate_rejects_malformed_condition(tmp_path):
    state, tools = _tools(tmp_path)
    for bad in ("flag", {}, {"flags": "x"}, {"var": "g"}):
        assert tools["add_gate"](bad, node_id="n2", choice_index=0)["ok"] is False


# ── remove_gate: delete one requires, nothing else ─────────────────────────────

def test_remove_gate(tmp_path):
    state, tools = _tools(tmp_path)
    r = tools["remove_gate"](node_id="n1", choice_index=1)
    assert r["ok"] is True
    ch = state.read_component("nodes")["nodes"]["n1"]["end"]["choices"][1]
    assert "requires" not in ch and ch["target"] == "n1"
    assert tools["remove_gate"](node_id="n1", choice_index=0)["ok"] is False   # nothing to remove
    r = tools["remove_gate"](place_id="r1", interactable_id="h_move_gated")
    assert r["ok"] is True
    a = _action(state, "h_move_gated")
    assert "requires" not in a and a["target"] == "r1"


# ── the wiring fixes run on the additive scope, never the replace-shaped edits ──

def test_wiring_scopes_are_additive():
    from maestro.modules.state import _WIRING_TOOLS
    from maestro.modules.objectives import _WIRE_TOOLS
    from maestro.modules.scenes import Scenes
    assert {"add_effect", "add_gate", "remove_effect", "remove_gate"} <= set(_WIRING_TOOLS)
    assert not {"edit_node", "edit_place"} & set(_WIRING_TOOLS)
    assert "add_effect" in _WIRE_TOOLS and not {"edit_node", "edit_place"} & _WIRE_TOOLS
    dead_gate = next(c for c in Scenes.checks if c.code == "no_dead_gates")
    assert dead_gate.tools == frozenset({"read_node", "add_effect", "remove_gate"})
