import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from maestro.spec import Spec
from maestro.state import RunState
from maestro.executor import Executor, SpecNotFrozenError, pick_target


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


def test_emits_progress_events(tmp_path):
    state = RunState(tmp_path)

    def decide(ctx):
        outstanding = {f["component_id"] for f in ctx["todo"]}
        if "premise" in outstanding:
            return {"tool": "write_component", "args": {
                "component_id": "premise",
                "content": {"central_question": "Q?", "characters": [{"id": "a"}, {"id": "b"}]},
            }}
        return {"tool": "write_component", "args": {
            "component_id": "graph", "content": {"nodes": [{"id": "n1"}]}}}

    events = []
    ex = Executor(_spec(), state, _tools(state), decide, max_steps=10,
                  on_event=events.append)
    ex.run()

    types = [e["type"] for e in events]
    assert types[0] == "build_started"
    assert types.count("build_step") == 2
    assert types[-1] == "build_done"
    assert events[-1]["ok"] is True
    # build_step carries the active mode + shrinking failure count.
    steps = [e for e in events if e["type"] == "build_step"]
    assert steps[0]["step"] == 1 and "n_failing" in steps[0] and "mode" in steps[0]


def _fail(check_type, detail="x", cid="node_scripts"):
    return {"component_id": cid, "check": {"type": check_type}, "detail": detail}


def test_pick_target_priority_order():
    # Build before polish: nodes on the page (count) first, lint (compiles) last so one
    # stubborn syntax error can't starve node creation.
    fails = [_fail("each_node_min_lines"), _fail("reachable_from_start"),
             _fail("count"), _fail("compiles")]
    assert pick_target(fails)["check"]["type"] == "count"
    assert pick_target([_fail("each_node_min_lines"), _fail("count")])["check"]["type"] == "count"
    assert pick_target([_fail("all_characters_speak"),
                        _fail("reachable_from_start")])["check"]["type"] == "reachable_from_start"
    # compiles is dead last — only targeted when nothing else fails.
    assert pick_target([_fail("compiles"), _fail("each_has")])["check"]["type"] == "each_has"


def test_executor_drives_target_via_sub_runner(tmp_path):
    state = RunState(tmp_path)
    state.write_component("premise", {"central_question": "Q?"})
    spec = Spec({"title": "T", "frozen": True, "components": [
        {"id": "premise", "deps": [], "done_conditions": [
            {"type": "exists", "path": "premise.central_question"}]},
        {"id": "node_scripts", "deps": ["premise"], "done_conditions": [
            {"type": "count", "path": "node_scripts.node_ids", "min": 2}]},
    ]})

    def write_two(**kw):
        state.write_component("node_scripts", {"node_ids": ["a", "b"], "scripts": {}})
        return {"ok": True}

    seen = []

    def runner(target, context, dispatch, target_met, report, budget, view_fn):
        seen.append((target["component_id"], target["check"]["type"]))
        dispatch({"tool": "write_two", "args": {}})
        report("write_two: ok")
        assert target_met()  # the write satisfied the target

    ex = Executor(spec, state, {"write_two": write_two}, decide=lambda c: {}, max_steps=5,
                  sub_runners={"node_scripts": runner})
    result = ex.run()

    assert result.ok is True
    assert seen == [("node_scripts", "count")]   # premise already passed → only node target


def test_is_stalling_on_repeated_reads(tmp_path):
    from maestro.executor import StepRecord
    ex = Executor(_spec(), RunState(tmp_path), _tools(RunState(tmp_path)), decide=lambda c: {})
    read = {"tool": "read_node", "args": {"node_id": "s1"}}
    write = {"tool": "write_node", "args": {"node_id": "s1"}}

    assert ex._is_stalling([StepRecord(1, read, "")]) is False           # need 2
    assert ex._is_stalling([StepRecord(1, read, ""), StepRecord(2, read, "")]) is True
    # different target → not stalling
    read2 = {"tool": "read_node", "args": {"node_id": "s2"}}
    assert ex._is_stalling([StepRecord(1, read, ""), StepRecord(2, read2, "")]) is False
    # repeated writes are progress, not a stall
    assert ex._is_stalling([StepRecord(1, write, ""), StepRecord(2, write, "")]) is False


def test_read_payload_surfaced_then_cleared(tmp_path):
    ex = Executor(_spec(), RunState(tmp_path), {}, decide=lambda c: {})
    read = {"tool": "read_node", "args": {"node_id": "s1"}}
    payload = ex._read_payload(read, {"ok": True, "content": 'label s1:\n    a "hi"'})
    assert payload and 'a "hi"' in payload
    # a write returns no payload → last_read clears
    assert ex._read_payload({"tool": "write_node", "args": {}}, {"ok": True}) is None


def test_context_is_rebuilt_from_durable_state(tmp_path):
    state = RunState(tmp_path)
    state.write_component("premise", {"central_question": "Q?", "characters": [{"id": "a"}, {"id": "b"}]})
    ex = Executor(_spec(), state, _tools(state), decide=lambda ctx: {})

    ctx = ex.build_context()
    # premise already satisfied on disk → only graph is outstanding in the to-do
    assert {f["component_id"] for f in ctx["todo"]} == {"graph"}
    assert ctx["available_tools"] == ["write_component"]
