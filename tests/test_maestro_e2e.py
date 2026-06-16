import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from maestro.spec import Spec
from maestro.state import RunState
from maestro.tools import build_tools
from maestro.executor import Executor
from renpy.component_schemas import SCHEMAS


def test_full_loop_builds_a_launchable_project(tmp_path, monkeypatch):
    """Scripted agent authors valid content; executor drives to a passing compiles
    check and a real script.rpy on disk. Proves the stack end-to-end without an LLM."""
    import renpy.fns as fns
    monkeypatch.setattr(fns, "_get_sdk_path", lambda: "")   # no lint/distribute subprocess

    state = RunState(tmp_path)
    spec = Spec({"title": "Noir", "frozen": True, "components": [
        {"id": "premise", "deps": [], "done_conditions": [
            {"type": "each_has", "path": "premise.characters", "fields": ["id", "name"]}]},
        {"id": "asset_manifest", "deps": [], "done_conditions": [
            {"type": "exists", "path": "asset_manifest.backgrounds"}]},
        {"id": "node_scripts", "deps": ["premise", "asset_manifest"], "done_conditions": [
            {"type": "count", "path": "node_scripts.node_ids", "min": 1},
            {"type": "compiles"}]},
    ]})
    tools = build_tools(spec, state, schemas=SCHEMAS)

    def decide(ctx):
        outstanding = {f["component_id"] for f in ctx["todo"]}
        if "premise" in outstanding:
            return {"tool": "write_component", "args": {"component_id": "premise", "content": {
                "central_question": "Will love survive?",
                "characters": [{"id": "evelyn", "name": "Evelyn", "color": "#c8ffc8"}],
                "endings": [{"id": "ending_good"}]}}}
        if "asset_manifest" in outstanding:
            return {"tool": "write_component", "args": {"component_id": "asset_manifest", "content": {
                "backgrounds": [{"id": "bg_office", "image_file": "office.png"}],
                "characters": [{"id": "evelyn", "image_file": "evelyn.png"}],
                "cgs": [], "title_card": {}}}}
        if "node_scripts" in outstanding:
            return {"tool": "write_node", "args": {
                "node_id": "scene_01",
                "content": "label scene_01:\n    scene bg_office\n    evelyn \"It begins.\"\n    return",
                "story_state_delta": {"event_summary": "the opening", "new_facts": ["a case begins"]}}}
        return {}

    result = Executor(spec, state, tools, decide, max_steps=10).run()

    assert result.ok is True, result.failures
    script = tmp_path / "game_output" / "game" / "script.rpy"
    assert script.exists()
    body = script.read_text(encoding="utf-8")
    assert "label start:" in body and "jump scene_01" in body
    # story-state delta from write_node was recorded
    assert "a case begins" in state.read_story_state()["established_facts"]


def test_full_loop_builds_branching_multi_scene_game(tmp_path, monkeypatch):
    """A spec demanding depth + branching + multiple endings drives a real game:
    several reachable nodes, menu choices, distinct endings, every character speaking."""
    import renpy.fns as fns
    monkeypatch.setattr(fns, "_get_sdk_path", lambda: "")
    from renpy.checks import register_all
    register_all()   # make reachable_from_start / min_branches / ... available

    state = RunState(tmp_path)
    spec = Spec({"title": "Noir", "frozen": True, "components": [
        {"id": "premise", "deps": [], "done_conditions": [
            {"type": "count", "path": "premise.characters", "min": 3},
            {"type": "count", "path": "premise.endings", "min": 3},
            {"type": "distinct", "path": "premise.endings", "key": "id"}]},
        {"id": "asset_manifest", "deps": [], "done_conditions": [
            {"type": "exists", "path": "asset_manifest.backgrounds"}]},
        {"id": "node_scripts", "deps": ["premise", "asset_manifest"], "done_conditions": [
            {"type": "count", "path": "node_scripts.node_ids", "min": 4},
            {"type": "refs_resolve", "from": "premise.endings", "from_key": "id",
             "to": "node_scripts.node_ids"},
            {"type": "reachable_from_start"},
            {"type": "min_branches", "min": 1},
            {"type": "each_node_min_lines", "min": 2},
            {"type": "all_characters_speak"},
            {"type": "compiles"}]},
    ]})
    tools = build_tools(spec, state, schemas=SCHEMAS)

    nodes = {
        "scene_01": 'label scene_01:\n    scene bg_office\n    show evelyn\n    evelyn "You came back."\n    show jack\n    jack "I had to."\n    menu:\n        "Stay":\n            jump scene_02\n        "Leave":\n            jump ending_bad',
        "scene_02": 'label scene_02:\n    show lila\n    lila "Choose wisely."\n    evelyn "I will."\n    menu:\n        "Good":\n            jump ending_good\n        "Neutral":\n            jump ending_neutral',
        "ending_good": 'label ending_good:\n    evelyn "We made it."\n    "The end."\n    return',
        "ending_bad": 'label ending_bad:\n    evelyn "Gone."\n    "Fade out."\n    return',
        "ending_neutral": 'label ending_neutral:\n    evelyn "Someday."\n    "Maybe."\n    return',
    }

    def decide(ctx):
        out = {f["component_id"] for f in ctx["todo"]}
        if "premise" in out:
            return {"tool": "write_component", "args": {"component_id": "premise", "content": {
                "central_question": "Will they stay?",
                "characters": [{"id": "evelyn", "name": "Evelyn", "voice": "soft"},
                               {"id": "jack", "name": "Jack", "voice": "gruff"},
                               {"id": "lila", "name": "Lila", "voice": "sly"}],
                "endings": [{"id": "ending_good"}, {"id": "ending_bad"}, {"id": "ending_neutral"}]}}}
        if "asset_manifest" in out:
            return {"tool": "write_component", "args": {"component_id": "asset_manifest", "content": {
                "backgrounds": [{"id": "bg_office", "image_file": "office.png"}],
                "characters": [{"id": "evelyn", "image_file": "evelyn.png"},
                               {"id": "jack", "image_file": "jack.png"},
                               {"id": "lila", "image_file": "lila.png"}],
                "cgs": [], "title_card": {}}}}
        if "node_scripts" in out:
            for nid, content in nodes.items():
                if nid not in (state.read_component("node_scripts") or {}).get("scripts", {}):
                    return {"tool": "write_node", "args": {"node_id": nid, "content": content}}
        return {}

    result = Executor(spec, state, tools, decide, max_steps=20).run()
    assert result.ok is True, result.failures

    body = (tmp_path / "game_output" / "game" / "script.rpy").read_text(encoding="utf-8")
    assert "menu:" in body
    assert "label ending_good:" in body and "label ending_bad:" in body


def test_invalid_content_becomes_steering_signal_not_crash(tmp_path, monkeypatch):
    """If the agent writes the wrong shape, write_component rejects it and the loop
    keeps going (no crash); a stubborn agent simply never reaches done."""
    import renpy.fns as fns
    monkeypatch.setattr(fns, "_get_sdk_path", lambda: "")

    state = RunState(tmp_path)
    spec = Spec({"frozen": True, "components": [
        {"id": "premise", "done_conditions": [
            {"type": "each_has", "path": "premise.characters", "fields": ["id", "name"]}]}]})
    tools = build_tools(spec, state, schemas=SCHEMAS)

    seen = []

    def decide(ctx):
        seen.append(ctx["last_result"])
        # Always writes the bad shape from the real failing run (no id).
        return {"tool": "write_component", "args": {
            "component_id": "premise", "content": {"characters": [{"name": "NoId"}]}}}

    result = Executor(spec, state, tools, decide, max_steps=3).run()
    assert result.ok is False                       # never converged, but never crashed
    assert any(s and "invalid premise" in s for s in seen if s)  # got the steering signal
