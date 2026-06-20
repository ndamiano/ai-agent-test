import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from maestro.spec import Spec
from maestro.state import RunState
from maestro.tools import build_tools
from maestro.executor import Executor
from renpy.component_schemas import SCHEMAS


def _no_sdk(monkeypatch):
    # compile_ir resolves _get_sdk_path at its own module scope; patch it there so the
    # compile check skips the lint/distribute subprocess but still writes the project.
    import renpy.ir_compiler as ir_compiler
    monkeypatch.setattr(ir_compiler, "_get_sdk_path", lambda: "")


def test_full_loop_builds_a_launchable_project(tmp_path, monkeypatch):
    """Scripted agent authors valid IR; executor drives to a passing compiles check and a
    real script.rpy on disk. Proves the stack end-to-end without an LLM or the SDK."""
    _no_sdk(monkeypatch)
    state = RunState(tmp_path)
    spec = Spec({"title": "Noir", "genre": "vn", "frozen": True, "components": [
        {"id": "premise", "deps": [], "done_conditions": [
            {"type": "each_has", "path": "premise.characters", "fields": ["id", "name"]}]},
        {"id": "asset_manifest", "deps": [], "done_conditions": [
            {"type": "exists", "path": "asset_manifest.backgrounds"}]},
        {"id": "nodes", "deps": ["premise", "asset_manifest"], "done_conditions": [
            {"type": "count", "path": "nodes.node_ids", "min": 1},
            {"type": "compiles"}]},
    ]})
    tools = build_tools(spec, state, schemas=SCHEMAS)

    def decide(ctx):
        out = {f["component_id"] for f in ctx["todo"]}
        if "premise" in out:
            return {"tool": "write_component", "args": {"component_id": "premise", "content": {
                "central_question": "Will love survive?",
                "characters": [{"id": "evelyn", "name": "Evelyn"}],
                "endings": [{"id": "ending_good"}]}}}
        if "asset_manifest" in out:
            return {"tool": "write_component", "args": {"component_id": "asset_manifest", "content": {
                "backgrounds": [{"id": "bg_office", "image_file": "office.png"}],
                "characters": [{"id": "evelyn", "image_file": "evelyn.png"}],
                "cgs": [], "title_card": {}}}}
        if "nodes" in out:
            return {"tool": "write_node", "args": {
                "node_id": "scene_01",
                "content": {"lines": [{"speaker": "evelyn", "text": "It begins."}],
                            "end": {"type": "return"}},
                "story_state_delta": {"event_summary": "the opening", "new_facts": ["a case begins"]}}}
        return {}

    result = Executor(spec, state, tools, decide, max_steps=10).run()
    assert result.ok is True, result.failures
    body = (tmp_path / "game_output" / "game" / "script.rpy").read_text(encoding="utf-8")
    assert "label start:" in body and "jump scene_01" in body
    assert "a case begins" in state.read_story_state()["established_facts"]


def test_full_loop_builds_branching_multi_scene_game(tmp_path, monkeypatch):
    """A spec demanding depth + branching + multiple endings drives a real game: several
    reachable nodes, menu choices, distinct endings, every character speaking."""
    _no_sdk(monkeypatch)
    from renpy.ir_checks import register_all
    register_all()

    state = RunState(tmp_path)
    spec = Spec({"title": "Noir", "genre": "vn", "frozen": True, "components": [
        {"id": "premise", "deps": [], "done_conditions": [
            {"type": "count", "path": "premise.characters", "min": 3},
            {"type": "count", "path": "premise.endings", "min": 3},
            {"type": "distinct", "path": "premise.endings", "key": "id"}]},
        {"id": "asset_manifest", "deps": [], "done_conditions": [
            {"type": "exists", "path": "asset_manifest.backgrounds"}]},
        {"id": "nodes", "deps": ["premise", "asset_manifest"], "done_conditions": [
            {"type": "count", "path": "nodes.node_ids", "min": 4},
            {"type": "refs_resolve", "from": "premise.endings", "from_key": "id", "to": "nodes.node_ids"},
            {"type": "reachable_from_start"},
            {"type": "min_branches", "min": 1},
            {"type": "each_node_min_lines", "min": 2},
            {"type": "all_characters_speak"},
            {"type": "compiles"}]},
    ]})
    tools = build_tools(spec, state, schemas=SCHEMAS)

    nodes = {
        "scene_01": {"lines": [{"speaker": "evelyn", "text": "You came back."},
                               {"speaker": "jack", "text": "I had to."}],
                     "end": {"type": "menu", "choices": [{"text": "Stay", "target": "scene_02"},
                                                         {"text": "Leave", "target": "ending_bad"}]}},
        "scene_02": {"lines": [{"speaker": "lila", "text": "Choose wisely."},
                               {"speaker": "evelyn", "text": "I will."}],
                     "end": {"type": "menu", "choices": [{"text": "Good", "target": "ending_good"},
                                                         {"text": "Neutral", "target": "ending_neutral"}]}},
        "ending_good": {"lines": [{"speaker": "evelyn", "text": "We made it."},
                                  {"speaker": None, "text": "The end."}], "end": {"type": "end"}},
        "ending_bad": {"lines": [{"speaker": "evelyn", "text": "Gone."},
                                 {"speaker": None, "text": "Fade out."}], "end": {"type": "end"}},
        "ending_neutral": {"lines": [{"speaker": "evelyn", "text": "Someday."},
                                     {"speaker": None, "text": "Maybe."}], "end": {"type": "end"}},
    }

    def decide(ctx):
        out = {f["component_id"] for f in ctx["todo"]}
        if "premise" in out:
            return {"tool": "write_component", "args": {"component_id": "premise", "content": {
                "central_question": "Will they stay?",
                "characters": [{"id": "evelyn", "name": "Evelyn"}, {"id": "jack", "name": "Jack"},
                               {"id": "lila", "name": "Lila"}],
                "endings": [{"id": "ending_good"}, {"id": "ending_bad"}, {"id": "ending_neutral"}]}}}
        if "asset_manifest" in out:
            return {"tool": "write_component", "args": {"component_id": "asset_manifest", "content": {
                "backgrounds": [{"id": "bg_office", "image_file": "office.png"}],
                "characters": [{"id": "evelyn"}, {"id": "jack"}, {"id": "lila"}],
                "cgs": [], "title_card": {}}}}
        if "nodes" in out:
            written = (state.read_component("nodes") or {}).get("nodes", {})
            for nid, content in nodes.items():
                if nid not in written:
                    return {"tool": "write_node", "args": {"node_id": nid, "content": content}}
        return {}

    result = Executor(spec, state, tools, decide, max_steps=20).run()
    assert result.ok is True, result.failures
    body = (tmp_path / "game_output" / "game" / "script.rpy").read_text(encoding="utf-8")
    assert "menu:" in body
    assert "label ending_good:" in body and "label ending_bad:" in body


def test_invalid_content_becomes_steering_signal_not_crash(tmp_path, monkeypatch):
    """Wrong shape is rejected by write_component and the loop keeps going (no crash)."""
    _no_sdk(monkeypatch)
    state = RunState(tmp_path)
    spec = Spec({"frozen": True, "components": [
        {"id": "premise", "done_conditions": [
            {"type": "each_has", "path": "premise.characters", "fields": ["id", "name"]}]}]})
    tools = build_tools(spec, state, schemas=SCHEMAS)

    seen = []

    def decide(ctx):
        seen.append(ctx["last_result"])
        return {"tool": "write_component", "args": {
            "component_id": "premise", "content": {"characters": [{"name": "NoId"}]}}}

    result = Executor(spec, state, tools, decide, max_steps=3).run()
    assert result.ok is False
    assert any(s and "invalid premise" in s for s in seen if s)
