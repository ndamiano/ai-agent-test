import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from maestro.spec import Spec
from maestro.state import RunState
from maestro.tools import build_tools, SpecNotFrozen
from maestro.executor import Executor


def _spec(frozen=True):
    return Spec({"title": "T", "frozen": frozen, "components": [
        {"id": "premise", "deps": [], "done_conditions": [
            {"type": "exists", "path": "premise.central_question"},
            {"type": "count", "path": "premise.characters", "min": 2},
        ]},
    ]})


def test_write_component_refuses_when_unfrozen(tmp_path):
    state = RunState(tmp_path)
    tools = build_tools(_spec(frozen=False), state)
    with pytest.raises(SpecNotFrozen):
        tools["write_component"]("premise", {"x": 1})


def test_write_and_read_component(tmp_path):
    state = RunState(tmp_path)
    tools = build_tools(_spec(), state)
    assert tools["write_component"]("premise", {"central_question": "Q?"})["ok"] is True
    got = tools["read_component"]("premise")
    assert got["ok"] is True and got["content"]["central_question"] == "Q?"
    assert tools["read_component"]("missing")["ok"] is False


def test_validate_tool_reports_failures(tmp_path):
    state = RunState(tmp_path)
    tools = build_tools(_spec(), state)
    res = tools["validate"]()
    assert res["ok"] is False
    assert {f["component_id"] for f in res["failures"]} == {"premise"}

    tools["write_component"]("premise", {"central_question": "Q?", "characters": [{"id": "a"}, {"id": "b"}]})
    assert tools["validate"]()["ok"] is True


def test_compile_renpy_tool_delegates(tmp_path, monkeypatch):
    import renpy.compiler as compiler
    monkeypatch.setattr(compiler, "compile_renpy", lambda wd, **kw: {"ok": True, "reason": None})
    tools = build_tools(_spec(), RunState(tmp_path))
    assert tools["compile_renpy"]()["ok"] is True


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
    assert res["status"] == "review_requested"
    assert res["options"] == ["romance", "tragedy"]


def test_executor_drives_real_tools_to_completion(tmp_path):
    """Phase 3 + 4 together: executor + real artifact tools, agent authors content."""
    state = RunState(tmp_path)
    spec = _spec()
    tools = build_tools(spec, state)

    def decide(ctx):
        if any(f["component_id"] == "premise" for f in ctx["todo"]):
            return {"tool": "write_component", "args": {
                "component_id": "premise",
                "content": {"central_question": "Will it hold?", "characters": [{"id": "a"}, {"id": "b"}]},
            }}
        return {}

    result = Executor(spec, state, tools, decide, max_steps=5).run()
    assert result.ok is True
    assert result.failures == []


def test_write_node_accepts_flat_delta_kwargs(tmp_path):
    # The model passes delta fields flat (entity_updates=...) instead of nested; this
    # used to crash with TypeError. Now folded into the delta.
    state = RunState(tmp_path)
    tools = build_tools(_spec(), state)
    res = tools["write_node"]("scene_01", "label scene_01:\n    return",
                              new_facts=["a clue surfaced"],
                              entity_updates={"mara": {"trust": "wary"}})
    assert res["ok"] is True
    ss = state.read_story_state()
    assert "a clue surfaced" in ss["established_facts"]
    assert ss["entity_states"]["mara"]["trust"] == "wary"


def test_write_node_tolerates_malformed_delta(tmp_path):
    # The model sometimes passes story_state_delta as a list/string, not a dict.
    # Must not crash (this hit a ValueError live).
    state = RunState(tmp_path)
    tools = build_tools(_spec(), state)
    assert tools["write_node"]("scene_01", "label scene_01:\n    return",
                               story_state_delta=["new_facts"])["ok"] is True
    assert tools["write_node"]("scene_02", "label scene_02:\n    return",
                               story_state_delta="oops")["ok"] is True


def test_write_node_normalizes_over_escaped_script(tmp_path):
    # Model over-escaped: literal \n and \" instead of real newline/quote. Normalize it.
    state = RunState(tmp_path)
    tools = build_tools(_spec(), state)
    tools["write_node"]("scene_01", 'label scene_01:\\n    evelyn \\"Hi.\\"\\n    return')
    script = state.read_component("node_scripts")["scripts"]["scene_01"]
    assert "\\n" not in script and '\\"' not in script
    assert script == 'label scene_01:\n    evelyn "Hi."\n    return'


def test_write_component_normalizes_node_scripts(tmp_path):
    state = RunState(tmp_path)
    tools = build_tools(_spec(), state)
    tools["write_component"]("node_scripts", {
        "node_ids": ["s1"], "scripts": {"s1": 'label s1:\\n    a \\"hi\\"\\n    return'}})
    s = state.read_component("node_scripts")["scripts"]["s1"]
    assert s == 'label s1:\n    a "hi"\n    return'
