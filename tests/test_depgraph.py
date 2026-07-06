"""Epic B — propagation: the reified dependency graph (B1), the transitive downstream-dirty walk
(B2), and the edit/rewrite hooks that drive it (B3). Behaviour/contract level — no live services.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from maestro.state import RunState
from maestro.tools import build_tools
from maestro.rewrite import rewrite_node
from maestro.modules import human as human_mod
from maestro.depgraph import build_dependency_graph, downstream_closure, mark_downstream_dirty


def _spec():
    return {"title": "T", "frozen": True, "modules": [], "params": {}}


# ── B1: reference edges — direction ─────────────────────────────────────────────
def test_node_jump_dirties_the_jumper_not_the_target():
    # scene_a jumps to scene_b: scene_a's flow ASSUMES scene_b's shape, so editing scene_b
    # should dirty scene_a (the referencer) — not the other way around.
    artifact = {"nodes": {"node_ids": ["scene_a", "scene_b"], "nodes": {
        "scene_a": {"lines": [], "end": {"type": "jump", "target": "scene_b"}},
        "scene_b": {"lines": [], "end": {"type": "end"}},
    }}}
    graph = build_dependency_graph(artifact)
    assert graph["nodes:scene_b"] == {"nodes:scene_a"}
    assert "nodes:scene_a" not in graph   # editing the jumper doesn't dirty its target


def test_node_menu_targets_and_speaker_edges():
    artifact = {"nodes": {"node_ids": ["hub", "path_a", "path_b"], "nodes": {
        "hub": {"lines": [{"speaker": "mara", "text": "hi"}], "end": {"type": "menu", "choices": [
            {"text": "go a", "target": "path_a"}, {"text": "go b", "target": "path_b"}]}},
        "path_a": {"lines": [], "end": {"type": "end"}},
        "path_b": {"lines": [], "end": {"type": "end"}},
    }}}
    graph = build_dependency_graph(artifact)
    assert graph["nodes:path_a"] == {"nodes:hub"}
    assert graph["nodes:path_b"] == {"nodes:hub"}
    # editing the CHARACTER who speaks in hub dirties hub, not vice versa
    assert graph["characters:mara"] == {"nodes:hub"}


def test_place_move_and_talk_edges():
    artifact = {"places": {"place_ids": ["room_a", "room_b"], "places": {
        "room_a": {"interactables": [
            {"id": "door", "action": {"type": "move", "target": "room_b"}},
            {"id": "npc", "action": {"type": "talk", "node": "n_greet"}},
        ]},
        "room_b": {"interactables": []},
    }}, "nodes": {"node_ids": ["n_greet"], "nodes": {"n_greet": {"lines": [], "end": {"type": "end"}}}}}
    graph = build_dependency_graph(artifact)
    assert graph["places:room_b"] == {"places:room_a"}
    assert graph["nodes:n_greet"] == {"places:room_a"}


def test_combat_reference_edges_direction():
    artifact = {"combat": {
        "abilities": [{"id": "firebolt", "effects": [{"stat": "hp", "op": "sub"}]}],
        "combatants": [{"id": "cb_hero", "character": "mara", "abilities": ["firebolt"],
                        "stats": [{"stat": "hp", "value": 10}]}],
        "encounters": [{"id": "enc_1", "combatants": [{"ref": "cb_hero", "faction": "player"}]}],
    }}
    graph = build_dependency_graph(artifact)
    # editing the ability dirties the combatant that carries it
    assert "combat:cb_hero" in graph["combat:firebolt"]
    # editing the stat dirties both the ability (effect) and the combatant (stat block)
    assert graph["combat:hp"] == {"combat:firebolt", "combat:cb_hero"}
    # editing the character dirties the combatant
    assert graph["characters:mara"] == {"combat:cb_hero"}
    # editing the combatant dirties the encounter that references it
    assert graph["combat:cb_hero"] == {"combat:enc_1"}


# ── B1: state-flow edges — direction ────────────────────────────────────────────
def test_flag_setter_dirties_reader_not_vice_versa():
    artifact = {"nodes": {"node_ids": ["setter", "reader", "elsewhere"], "nodes": {
        "setter": {"lines": [{"speaker": None, "text": "x", "effects": [{"set_flag": "has_key"}]}],
                  "end": {"type": "end"}},
        "reader": {"lines": [], "end": {"type": "menu", "choices": [
            {"text": "use key", "target": "elsewhere", "requires": {"flag": "has_key"}}]}},
        "elsewhere": {"lines": [], "end": {"type": "end"}},
    }}}
    graph = build_dependency_graph(artifact)
    # setter produces has_key, reader consumes it -> editing setter dirties reader
    assert graph["nodes:setter"] == {"nodes:reader"}
    # reader has its OWN dependents (its jump target) — a distinct edge from the flag-flow one —
    # but reader is never itself listed as a dependency source pointing back at setter
    assert graph.get("nodes:elsewhere") == {"nodes:reader"}
    assert "nodes:setter" not in graph.get("nodes:reader", set())


def test_item_take_and_use_edges_plus_catalog_edit():
    artifact = {
        "items": {"items": [{"id": "key", "name": "Key"}]},
        "places": {"place_ids": ["room_a", "room_b"], "places": {
            "room_a": {"interactables": [{"id": "chest", "action": {"type": "take", "item": "key"}}]},
            "room_b": {"interactables": [{"id": "gate", "action": {"type": "use", "clauses": [
                {"requires": {"item": "key"}, "outcome": {"effects": []}}]}}]},
        }},
    }
    graph = build_dependency_graph(artifact)
    # editing the place that HANDS OUT the item dirties the place that GATES on it
    assert graph["places:room_a"] == {"places:room_b"}
    # editing the item's catalog entry dirties every place that touches it (producer + consumer)
    assert graph["items:key"] == {"places:room_a", "places:room_b"}


# ── B2: transitive closure + cycle safety ───────────────────────────────────────
def test_downstream_closure_is_transitive():
    graph = {"a": {"b"}, "b": {"c"}, "c": {"d"}}
    assert downstream_closure(graph, "a") == {"b", "c", "d"}


def test_downstream_closure_cycle_safe_and_excludes_self():
    graph = {"a": {"b"}, "b": {"a"}}   # a <-> b cycle
    assert downstream_closure(graph, "a") == {"b"}   # never includes 'a' itself


def test_mark_downstream_dirty_flags_whole_chain(tmp_path):
    state = RunState(tmp_path)
    state.write_component("nodes", {"node_ids": ["a", "b", "c"], "nodes": {
        "a": {"lines": [{"speaker": None, "text": "x", "effects": [{"set_flag": "f1"}]}],
             "end": {"type": "end"}},
        "b": {"lines": [{"speaker": None, "text": "y", "effects": [{"set_flag": "f2"}]}],
             "end": {"type": "menu", "choices": [{"text": "go", "target": "a",
                                                  "requires": {"flag": "f1"}}]}},
        "c": {"lines": [], "end": {"type": "menu", "choices": [{"text": "go", "target": "a",
                                                                "requires": {"flag": "f2"}}]}},
    }})

    flagged = mark_downstream_dirty(state, _spec(), "nodes:a")
    # a sets f1 -> b reads f1 -> b sets f2 -> c reads f2: editing a reflags the whole chain
    assert set(flagged) == {"nodes:b", "nodes:c"}
    assert {d["idkey"] for d in human_mod.dirty_entries(state)} == {"nodes:b", "nodes:c"}


def test_mark_downstream_dirty_never_flags_the_edited_asset(tmp_path):
    state = RunState(tmp_path)
    state.write_component("nodes", {"node_ids": ["a", "b"], "nodes": {
        "a": {"lines": [], "end": {"type": "jump", "target": "b"}},
        "b": {"lines": [], "end": {"type": "jump", "target": "a"}},   # a <-> b cycle
    }})
    flagged = mark_downstream_dirty(state, _spec(), "nodes:a")
    assert flagged == ["nodes:b"]
    assert "nodes:a" not in {d["idkey"] for d in human_mod.dirty_entries(state)}


def test_mark_downstream_dirty_preserves_a_specific_pending_note(tmp_path):
    state = RunState(tmp_path)
    state.write_component("nodes", {"node_ids": ["a", "b"], "nodes": {
        "a": {"lines": [], "end": {"type": "jump", "target": "b"}},
        "b": {"lines": [], "end": {"type": "end"}},
    }})
    human_mod.set_dirty(state, "nodes:a", "make the tone darker throughout")
    mark_downstream_dirty(state, _spec(), "nodes:b")
    entries = {d["idkey"]: d["note"] for d in human_mod.dirty_entries(state)}
    assert entries["nodes:a"] == "make the tone darker throughout"   # not clobbered


def test_mark_downstream_dirty_reflags_a_previously_cleared_asset(tmp_path):
    # "any edit reflags its entire downstream closure" — even a dependent blessed long ago.
    state = RunState(tmp_path)
    state.write_component("nodes", {"node_ids": ["a", "b"], "nodes": {
        "a": {"lines": [], "end": {"type": "jump", "target": "b"}},
        "b": {"lines": [], "end": {"type": "end"}},
    }})
    human_mod.set_dirty(state, "nodes:a", "")
    human_mod.clear_dirty(state, "nodes:a")   # blessed
    assert human_mod.dirty_entries(state) == []

    mark_downstream_dirty(state, _spec(), "nodes:b")
    assert {d["idkey"] for d in human_mod.dirty_entries(state)} == {"nodes:a"}


# ── B3: the hooks ────────────────────────────────────────────────────────────────
def _seed_two_node_chain(state):
    state.write_component("nodes", {"node_ids": ["a", "b"], "nodes": {
        "a": {"lines": [{"speaker": "mara", "text": "hi"}], "end": {"type": "end"}},
        "b": {"lines": [], "end": {"type": "jump", "target": "a"}},   # b depends on a
    }})


def test_edit_node_propagates_to_referencer(tmp_path):
    state = RunState(tmp_path)
    state.write_spec(_spec())
    _seed_two_node_chain(state)
    tools = build_tools(_spec(), state)

    res = tools["edit_node"]("a", line_index=0, text="hello there")
    assert res["ok"] is True
    entries = human_mod.dirty_entries(state)
    assert {d["idkey"] for d in entries} == {"nodes:b"}
    assert entries[0]["note"]   # a real instruction, not a blank note


def test_edit_node_full_content_replace_also_propagates(tmp_path):
    state = RunState(tmp_path)
    state.write_spec(_spec())
    _seed_two_node_chain(state)
    tools = build_tools(_spec(), state)

    new = {"lines": [{"speaker": "mara", "text": "brand new"}], "end": {"type": "end"}}
    res = tools["edit_node"]("a", content=new, force=True)
    assert res["ok"] is True
    assert "nodes:b" in {d["idkey"] for d in human_mod.dirty_entries(state)}


def test_edit_place_propagates(tmp_path):
    state = RunState(tmp_path)
    state.write_spec(_spec())
    state.write_component("places", {"place_ids": ["room_a", "room_b"], "places": {
        "room_a": {"interactables": [
            {"id": "door", "action": {"type": "move", "target": "room_b"}}]},
        "room_b": {"interactables": [
            {"id": "door_back", "action": {"type": "move", "target": "room_a"}}]},
    }})
    tools = build_tools(_spec(), state)

    res = tools["edit_place"]("room_b", "door_back", label="Sturdy Door")
    assert res["ok"] is True
    # editing room_b dirties whoever MOVES to room_b, i.e. room_a
    assert "places:room_a" in {d["idkey"] for d in human_mod.dirty_entries(state)}


def test_write_node_creating_a_new_node_does_not_propagate(tmp_path, monkeypatch):
    state = RunState(tmp_path)
    state.write_spec(_spec())
    _seed_two_node_chain(state)
    tools = build_tools(_spec(), state)

    calls = []
    monkeypatch.setattr("maestro.depgraph.mark_downstream_dirty",
                        lambda *a, **k: calls.append(a))
    res = tools["write_node"]("c", {"lines": [{"speaker": None, "text": "hi"}],
                                    "end": {"type": "end"}})
    assert res["ok"] is True
    assert calls == []   # authoring a brand-new asset never propagates


def test_write_component_does_not_propagate(tmp_path, monkeypatch):
    state = RunState(tmp_path)
    state.write_spec(_spec())
    tools = build_tools(_spec(), state)

    calls = []
    monkeypatch.setattr("maestro.depgraph.mark_downstream_dirty",
                        lambda *a, **k: calls.append(a))
    res = tools["write_component"]("notes", {"text": "hi"})
    assert res["ok"] is True
    assert calls == []


class _FakeConn:
    def __init__(self, content):
        self.content = content

    def generate_with_tools(self, messages, schemas, **kw):
        return {"choices": [{"message": {"tool_calls": [{"id": "1", "function": {
            "name": "write_node",
            "arguments": __import__("json").dumps({"node_id": "a", "content": self.content})}}]}}]}


def test_rewrite_node_propagates_to_referencer(tmp_path):
    state = RunState(tmp_path)
    state.write_spec(_spec())
    _seed_two_node_chain(state)
    spec = _spec()
    tools = build_tools(spec, state)
    rewritten = {"lines": [{"speaker": "mara", "text": "a wholly different line"}],
                "end": {"type": "end"}}

    res = rewrite_node(spec, state, "a", "make it colder", tools, connector=_FakeConn(rewritten))
    assert res["ok"] is True
    assert "nodes:b" in {d["idkey"] for d in human_mod.dirty_entries(state)}
