import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from maestro.spec import Spec
from maestro.state import RunState
from maestro.executor import Executor, SpecNotFrozenError


def _spec(frozen=True):
    return Spec({
        "title": "Test",
        "frozen": frozen,
        "components": [
            {"id": "premise", "deps": [], "done_conditions": [
                {"type": "exists", "path": "premise.central_question"},
                {"type": "count", "path": "premise.characters", "min": 2},
            ]},
            {"id": "graph", "deps": ["premise"], "done_conditions": [
                {"type": "exists", "path": "graph.nodes"},
            ]},
        ],
    })


def _tools(state):
    """Minimal in-memory tool set: write a component, and validate."""
    def write_component(component_id, content):
        state.write_component(component_id, content)
        return {"ok": True, "component_id": component_id}
    return {"write_component": write_component}


def test_refuses_when_spec_not_frozen(tmp_path):
    state = RunState(tmp_path)
    ex = Executor(_spec(frozen=False), state, _tools(state), decide=lambda ctx: {})
    with pytest.raises(SpecNotFrozenError):
        ex.run()


def test_reaches_completion_via_validate(tmp_path):
    state = RunState(tmp_path)

    # Scripted decider: address the first outstanding failure each step. Proves
    # the loop drives to an empty failure list, not that the agent is smart.
    def decide(ctx):
        outstanding = {f["component_id"] for f in ctx["todo"]}
        if "premise" in outstanding:
            return {"tool": "write_component", "args": {
                "component_id": "premise",
                "content": {"central_question": "Q?", "characters": [{"id": "a"}, {"id": "b"}]},
            }}
        if "graph" in outstanding:
            return {"tool": "write_component", "args": {
                "component_id": "graph", "content": {"nodes": [{"id": "n1"}]},
            }}
        return {}

    ex = Executor(_spec(), state, _tools(state), decide, max_steps=10)
    result = ex.run()

    assert result.ok is True
    assert result.steps == 2          # one write per component
    assert result.failures == []


def test_completion_guarantee_partial_is_not_done(tmp_path):
    state = RunState(tmp_path)

    # Decider only ever writes premise, never graph → must NOT report done.
    def decide(ctx):
        return {"tool": "write_component", "args": {
            "component_id": "premise",
            "content": {"central_question": "Q?", "characters": [{"id": "a"}, {"id": "b"}]},
        }}

    ex = Executor(_spec(), state, _tools(state), decide, max_steps=5)
    result = ex.run()

    assert result.ok is False
    assert result.steps == 5
    assert {f["component_id"] for f in result.failures} == {"graph"}


def test_done_not_faked_by_agent(tmp_path):
    state = RunState(tmp_path)

    # Agent does nothing useful (no-op actions). Executor must never declare done
    # while real failures remain — completion comes only from validate.
    ex = Executor(_spec(), state, _tools(state), decide=lambda ctx: {"tool": "write_component"},
                  max_steps=3)
    result = ex.run()
    assert result.ok is False
    assert len(result.failures) > 0


def test_context_is_rebuilt_from_durable_state(tmp_path):
    state = RunState(tmp_path)
    state.write_component("premise", {"central_question": "Q?", "characters": [{"id": "a"}, {"id": "b"}]})
    ex = Executor(_spec(), state, _tools(state), decide=lambda ctx: {})

    ctx = ex.build_context()
    # premise already satisfied on disk → only graph is outstanding in the to-do
    assert {f["component_id"] for f in ctx["todo"]} == {"graph"}
    assert ctx["available_tools"] == ["write_component"]
