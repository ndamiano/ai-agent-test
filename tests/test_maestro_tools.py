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
    monkeypatch.setattr(compiler, "compile_renpy", lambda wd: {"ok": True, "reason": None})
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
