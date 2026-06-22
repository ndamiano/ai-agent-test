"""outline — the VN beat-sheet stage between premise and nodes (quality_todo §2).

Proves the wiring: outline is composed into the `vn` preset, sits in build order after premise and
before nodes, its baseline forces a planned path to every ending, and the executor authors it in
its own mode before the nodes are written (so the node sub-loop sees the arc).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from maestro.spec import Spec
from maestro.state import RunState
from maestro.tools import build_tools
from maestro.executor import Executor
from maestro.validate import validate
from maestro.discrete.validators import v_outline
from renpy.component_schemas import SCHEMAS
from renpy.ir_checks import register_all

register_all()


def test_vn_preset_composes_outline_between_premise_and_nodes():
    from maestro.modules import compose, PRESETS
    c = compose(PRESETS["vn"].modules)
    assert c.components == ["premise", "asset_manifest", "outline", "nodes"]
    assert c.deps["outline"] == ["premise"]
    assert c.deps["nodes"] == ["premise", "asset_manifest", "outline"]
    assert c.mode_prompts["outline"] == "mode_outline.txt"
    # outline is a one-shot decider component (write_component), not a sub-loop like nodes.
    assert "outline" not in c.subloop_modules
    assert "write_component" in c.mode_tools["outline"]


def test_outline_validator_rejects_bad_shapes():
    assert v_outline({"beats": [{"id": "b1"}]}) is not None              # no logline
    assert v_outline({"logline": "x", "beats": []}) is not None          # empty beats
    assert v_outline({"logline": "x", "beats": [{"summary": "no id"}]}) is not None
    assert v_outline({"logline": "x", "beats": [{"id": "b1"}],
                      "ending_paths": [{"earned_by": "no ending id"}]}) is not None
    assert v_outline({"logline": "x", "beats": [{"id": "b1"}],
                      "ending_paths": [{"ending": "ending_a", "earned_by": "y"}]}) is None


def test_baseline_forces_a_path_for_every_ending(tmp_path):
    """The ending-coverage check fails until each premise ending has an outline path."""
    state = RunState(tmp_path)
    state.write_component("premise", {"central_question": "?", "characters": [{"id": "a", "name": "A"}],
                                      "endings": [{"id": "ending_a"}, {"id": "ending_b"}]})
    state.write_component("outline", {"logline": "spine", "ending_paths": [
        {"ending": "ending_a", "earned_by": "the a-path"}],  # ending_b uncovered
        "beats": [{"id": f"b{i}", "summary": "s", "purpose": "p", "tension": "t"} for i in range(5)]})
    cover = {"type": "refs_resolve", "from": "premise.endings", "from_key": "id",
             "to": "outline.ending_paths", "to_key": "ending"}
    spec = Spec({"frozen": True, "components": [{"id": "outline", "done_conditions": [cover]}]})

    fails = validate(spec, state)
    assert any("ending_b" in f["detail"] for f in fails)
    assert fails[0]["component_id"] == "outline"      # routes to the component that adds the path

    o = state.read_component("outline")
    o["ending_paths"].append({"ending": "ending_b", "earned_by": "the b-path"})
    state.write_component("outline", o)
    assert not validate(spec, state)


def test_executor_authors_outline_before_nodes(tmp_path, monkeypatch):
    """A scripted agent: the executor drives premise -> outline -> nodes, authoring the outline in
    its own mode before any node is written. (Node floor kept at 1 so the test isn't a full game.)"""
    import renpy.ir_compiler as ir_compiler
    monkeypatch.setattr(ir_compiler, "_get_sdk_path", lambda: "")

    state = RunState(tmp_path)
    outline_baseline = [
        {"type": "exists", "path": "outline.logline"},
        {"type": "count", "path": "outline.beats", "min": 5},
        {"type": "each_has", "path": "outline.beats", "fields": ["id", "summary", "purpose", "tension"]},
        {"type": "each_has", "path": "outline.ending_paths", "fields": ["ending", "earned_by"]},
        {"type": "refs_resolve", "from": "premise.endings", "from_key": "id",
         "to": "outline.ending_paths", "to_key": "ending"},
    ]
    spec = Spec({"title": "T", "genre": "vn", "frozen": True, "components": [
        {"id": "premise", "deps": [], "done_conditions": [
            {"type": "each_has", "path": "premise.characters", "fields": ["id", "name"]}]},
        {"id": "asset_manifest", "deps": ["premise"], "done_conditions": [
            {"type": "exists", "path": "asset_manifest.backgrounds"}]},
        {"id": "outline", "deps": ["premise"], "done_conditions": outline_baseline},
        {"id": "nodes", "deps": ["premise", "asset_manifest", "outline"], "done_conditions": [
            {"type": "count", "path": "nodes.node_ids", "min": 1},
            {"type": "compiles"}]},
    ]})
    tools = build_tools(spec, state, schemas=SCHEMAS)
    order = []

    def decide(ctx):
        mode = ctx["mode"]
        order.append(mode)
        if mode == "premise":
            return {"tool": "write_component", "args": {"component_id": "premise", "content": {
                "central_question": "stay or go?",
                "characters": [{"id": "ada", "name": "Ada"}],
                "endings": [{"id": "ending_stay"}, {"id": "ending_go"}]}}}
        if mode == "asset_manifest":
            return {"tool": "write_component", "args": {"component_id": "asset_manifest", "content": {
                "backgrounds": [{"id": "bg_room", "image_file": "room.png"}],
                "characters": [{"id": "ada", "image_file": "ada.png"}], "cgs": [], "title_card": {}}}}
        if mode == "outline":
            return {"tool": "write_component", "args": {"component_id": "outline", "content": {
                "logline": "Ada must choose between staying and leaving.",
                "beats": [{"id": f"beat_{i}", "summary": "s", "purpose": "p", "tension": "t"}
                          for i in range(5)],
                "ending_paths": [{"ending": "ending_stay", "earned_by": "she relents"},
                                 {"ending": "ending_go", "earned_by": "she breaks free"}]}}}
        if mode == "nodes":
            return {"tool": "write_node", "args": {"node_id": "scene_01", "content": {
                "lines": [{"speaker": "ada", "text": "It begins."}], "end": {"type": "return"}}}}
        return {}

    result = Executor(spec, state, tools, decide, max_steps=10).run()
    assert result.ok is True, result.failures
    assert state.read_component("outline")["logline"]
    # outline authored before the first node write
    assert order.index("outline") < order.index("nodes")
