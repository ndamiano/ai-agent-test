import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from maestro.spec import Spec
from maestro.state import RunState
from maestro.executor import Executor
from maestro.run_control import RunControl
from maestro import hitl


def _spec():
    return Spec({
        "title": "Test", "frozen": True,
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
    def write_component(component_id, content):
        state.write_component(component_id, content)
        return {"ok": True, "component_id": component_id}
    return {"write_component": write_component}


_PREMISE = {"central_question": "Q?", "characters": [{"id": "a"}, {"id": "b"}]}
_GRAPH_CHECK = {"type": "exists", "path": "graph.nodes"}


# ── pure functions ───────────────────────────────────────────────────────────
def test_todo_lifecycle(tmp_path):
    state = RunState(tmp_path)
    todo = hitl.add_todo(state, "premise", "make the villain scarier")
    assert [t["id"] for t in hitl.open_todos(state)] == [todo["id"]]
    assert hitl.resolve_todo(state, todo["id"]) is True
    assert hitl.open_todos(state) == []
    assert hitl.resolve_todo(state, "nope") is False


def test_waive_unwaive(tmp_path):
    state = RunState(tmp_path)
    w = hitl.waive(state, "graph", _GRAPH_CHECK)
    assert w["sig"] in hitl.waived_sigs(state)
    hitl.waive(state, "graph", _GRAPH_CHECK)  # idempotent
    assert len(state.read_waivers()) == 1
    assert hitl.unwaive(state, w["sig"]) is True
    assert hitl.waived_sigs(state) == set()
    assert hitl.unwaive(state, w["sig"]) is False


def test_effective_failures_merges_and_subtracts(tmp_path):
    state = RunState(tmp_path)
    state.write_component("premise", _PREMISE)  # premise passes, graph fails
    spec = _spec()

    fails = hitl.effective_failures(spec, state)
    assert {f["component_id"] for f in fails} == {"graph"}

    hitl.add_todo(state, "premise", "polish the opening")
    fails = hitl.effective_failures(spec, state)
    kinds = {f["check"]["type"] for f in fails}
    assert "human_todo" in kinds and "exists" in kinds

    hitl.waive(state, "graph", _GRAPH_CHECK)
    fails = hitl.effective_failures(spec, state)
    assert {f["check"]["type"] for f in fails} == {"human_todo"}  # machine check waived away


# ── executor integration ─────────────────────────────────────────────────────
def test_waived_check_completes_build(tmp_path):
    state = RunState(tmp_path)
    hitl.waive(state, "graph", _GRAPH_CHECK)  # accept the missing graph

    def decide(ctx):
        return {"tool": "write_component",
                "args": {"component_id": "premise", "content": _PREMISE}}

    ex = Executor(_spec(), state, _tools(state), decide, max_steps=5, control=RunControl())
    result = ex.run()

    assert result.ok is True
    assert state.read_component("graph") is None  # never built — the human waived it


def test_open_human_todo_parks_then_resolves(tmp_path):
    state = RunState(tmp_path)
    state.write_component("premise", _PREMISE)         # machine checks already pass
    state.write_component("graph", {"nodes": [{"id": "n1"}]})
    todo = hitl.add_todo(state, "premise", "make the villain scarier")

    control = RunControl()
    events = []
    ex = Executor(_spec(), state, _tools(state), decide=lambda c: {}, max_steps=5,
                  on_event=events.append, control=control)
    t = threading.Thread(target=ex.run, daemon=True)
    t.start()

    for _ in range(200):
        if control.status == "awaiting_human":
            break
        time.sleep(0.01)
    assert control.status == "awaiting_human"
    assert any(e["type"] == "awaiting_human" for e in events)
    assert t.is_alive()  # blocked on the human

    hitl.resolve_todo(state, todo["id"])
    t.join(timeout=5)
    assert not t.is_alive()

    types = [e["type"] for e in events]
    assert "human_cleared" in types
    assert types[-1] == "build_done"


def test_cancel_during_awaiting_human_unwinds(tmp_path):
    state = RunState(tmp_path)
    state.write_component("premise", _PREMISE)
    state.write_component("graph", {"nodes": [{"id": "n1"}]})
    hitl.add_todo(state, "premise", "open todo")

    control = RunControl()
    events = []
    ex = Executor(_spec(), state, _tools(state), decide=lambda c: {}, max_steps=5,
                  on_event=events.append, control=control)
    t = threading.Thread(target=ex.run, daemon=True)
    t.start()
    for _ in range(200):
        if control.status == "awaiting_human":
            break
        time.sleep(0.01)
    assert control.status == "awaiting_human"

    control.request_cancel()
    t.join(timeout=5)
    assert not t.is_alive()
    assert "build_cancelled" in [e["type"] for e in events]
