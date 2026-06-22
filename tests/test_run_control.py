import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from maestro.spec import Spec
from maestro.state import RunState
from maestro.executor import Executor
from maestro.run_control import RunControl, get_or_create, get, remove


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


def test_cancel_before_run_writes_nothing(tmp_path):
    state = RunState(tmp_path)
    control = RunControl()
    control.request_cancel()
    events = []
    ex = Executor(_spec(), state, _tools(state),
                  decide=lambda c: {"tool": "write_component",
                                    "args": {"component_id": "premise", "content": _PREMISE}},
                  max_steps=5, on_event=events.append, control=control)
    result = ex.run()

    assert result.ok is False
    assert any(e["type"] == "build_cancelled" for e in events)
    assert control.status == "cancelled"
    assert state.read_component("premise") is None  # unwound before the first step's work


def test_cancel_mid_build_halts_remaining_work(tmp_path):
    state = RunState(tmp_path)
    control = RunControl()

    def decide(ctx):
        outstanding = {f["component_id"] for f in ctx["todo"]}
        if "premise" in outstanding:
            control.request_cancel()  # cancel after this write lands
            return {"tool": "write_component",
                    "args": {"component_id": "premise", "content": _PREMISE}}
        return {"tool": "write_component",
                "args": {"component_id": "graph", "content": {"nodes": [{"id": "n1"}]}}}

    events = []
    ex = Executor(_spec(), state, _tools(state), decide, max_steps=10,
                  on_event=events.append, control=control)
    result = ex.run()

    assert result.ok is False
    assert "build_cancelled" in [e["type"] for e in events]
    assert state.read_component("premise") is not None  # the in-flight step finished
    assert state.read_component("graph") is None          # but the next never started


def test_pause_then_resume_completes(tmp_path):
    state = RunState(tmp_path)
    control = RunControl()
    paused_once = {"done": False}

    def decide(ctx):
        outstanding = {f["component_id"] for f in ctx["todo"]}
        if "premise" in outstanding:
            if not paused_once["done"]:
                control.request_pause()
                paused_once["done"] = True
            return {"tool": "write_component",
                    "args": {"component_id": "premise", "content": _PREMISE}}
        return {"tool": "write_component",
                "args": {"component_id": "graph", "content": {"nodes": [{"id": "n1"}]}}}

    events = []
    ex = Executor(_spec(), state, _tools(state), decide, max_steps=10,
                  on_event=events.append, control=control)
    t = threading.Thread(target=ex.run, daemon=True)
    t.start()

    for _ in range(200):  # wait until the loop parks at the pause boundary
        if control.status == "paused":
            break
        time.sleep(0.01)
    assert control.status == "paused"
    assert any(e["type"] == "build_paused" for e in events)
    assert state.read_component("graph") is None  # held — not built while paused

    control.request_resume()
    t.join(timeout=5)
    assert not t.is_alive()

    types = [e["type"] for e in events]
    assert "build_resumed" in types
    assert types[-1] == "build_done"
    assert state.read_component("graph") is not None


def test_registry_lifecycle():
    a = get_or_create("run-x")
    assert get("run-x") is a
    assert get_or_create("run-x") is a  # idempotent
    remove("run-x")
    assert get("run-x") is None
