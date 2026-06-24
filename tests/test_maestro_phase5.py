import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from maestro.spec import Spec
from maestro.state import RunState
from maestro import spec_tools
from maestro.executor import Executor
from maestro.tools import build_tools
from maestro.agent import _parse_action
from maestro.discrete.dialogue import _render_context


# ── propose_spec / amend_spec / freeze (run dir under tmp) ───────────────────

@pytest.fixture
def in_tmp_runs(tmp_path, monkeypatch):
    import tools.execution_context as ec
    monkeypatch.setattr(ec, "resolve_base_path", lambda p=None: tmp_path)
    return tmp_path


def test_propose_spec_persists_unfrozen(in_tmp_runs, monkeypatch):
    drafted = {"title": "Noir", "components": [{"id": "premise", "done_conditions": []}]}
    # Stub the LLM: json_with_correction (lazy-imported in propose_spec) returns our draft.
    import llm_clients.inference as inference
    monkeypatch.setattr(inference, "json_with_correction", lambda *a, **k: dict(drafted))

    spec = spec_tools.propose_spec("a noir romance", "run1")
    assert spec["frozen"] is False
    assert spec["request"] == "a noir romance"

    on_disk = RunState.for_run("run1").read_spec()
    assert on_disk["title"] == "Noir" and on_disk["frozen"] is False


def test_freeze_spec_sets_frozen(in_tmp_runs):
    state = RunState.for_run("run2")
    state.write_spec({"title": "T", "frozen": False, "components": []})
    spec_tools.freeze_spec("run2")
    assert RunState.for_run("run2").read_spec()["frozen"] is True


def test_amend_requires_reason(in_tmp_runs):
    state = RunState.for_run("run3")
    state.write_spec({"frozen": True, "components": []})
    with pytest.raises(ValueError):
        spec_tools.amend_spec("run3", {"title": "X"}, reason="")


def test_amend_unfreezes_and_applies(in_tmp_runs):
    state = RunState.for_run("run4")
    state.write_spec({"title": "Old", "frozen": True, "components": [
        {"id": "premise", "done_conditions": []},
    ]})
    res = spec_tools.amend_spec("run4", {
        "title": "New",
        "components": [{"id": "premise", "done_conditions": [{"type": "exists", "path": "premise.x"}]}],
    }, reason="title was wrong")

    assert res["status"] == "pending_human_approval"
    spec = RunState.for_run("run4").read_spec()
    assert spec["title"] == "New"
    assert spec["frozen"] is False          # the pause: build refuses until re-frozen
    assert spec["components"][0]["done_conditions"]  # component replaced by id


# ── executor milestone check-in ──────────────────────────────────────────────

def test_milestone_fires_once_per_component(tmp_path):
    state = RunState(tmp_path)
    spec = Spec({"frozen": True, "components": [
        {"id": "premise", "done_conditions": [{"type": "exists", "path": "premise.q"}]},
    ]})
    tools = build_tools(spec, state)
    fired = []

    def decide(ctx):
        if any(f["component_id"] == "premise" for f in ctx["todo"]):
            return {"tool": "write_component", "args": {"component_id": "premise", "content": {"q": "Q?"}}}
        return {}

    Executor(spec, state, tools, decide, max_steps=5, on_milestone=fired.append).run()
    assert fired == ["premise"]   # fired exactly once, on the failing→passing transition


# ── decider parsing (no live LLM) ────────────────────────────────────────────

def test_parse_action_extracts_tool_call():
    resp = {"choices": [{"message": {"tool_calls": [
        {"function": {"name": "write_component", "arguments": '{"component_id":"premise","content":{}}'}}]}}]}
    action = _parse_action(resp)
    assert action["tool"] == "write_component"
    assert action["args"]["component_id"] == "premise"


def test_parse_action_handles_no_tool_call():
    assert _parse_action({"choices": [{"message": {"content": "hi"}}]}) == {}
    assert _parse_action({"error": "boom"}) == {}


def test_parse_action_strips_code_fences():
    resp = {"choices": [{"message": {"tool_calls": [
        {"function": {"name": "validate", "arguments": '```json\n{}\n```'}}]}}]}
    assert _parse_action(resp) == {"tool": "validate", "args": {}}


def test_render_context_includes_todo():
    ctx = {"spec": {"title": "T"}, "todo": [
        {"component_id": "premise", "check": {"type": "exists"}, "detail": "premise.q missing"}],
        "scratchpad": {}, "last_result": None}
    text = _render_context(ctx)
    assert "premise.q missing" in text and "TO-DO" in text


# ── run_build wiring with a stubbed decider (no live LLM) ─────────────────────

def test_run_build_drives_to_completion(in_tmp_runs):
    from maestro import run as run_mod

    state = RunState.for_run("rb1")
    # 'blurb' is not a renpy schema-guarded component, so run_build's injected
    # schemas don't constrain it — this test exercises the run_build wiring only.
    state.write_spec({"title": "T", "frozen": True, "components": [
        {"id": "blurb", "done_conditions": [{"type": "exists", "path": "blurb.q"}]},
    ]})

    def decide(ctx):
        if ctx["todo"]:
            return {"tool": "write_component", "args": {"component_id": "blurb", "content": {"q": "Q?"}}}
        return {}

    result = run_mod.run_build("rb1", max_steps=5, decide=decide)
    assert result.ok is True
