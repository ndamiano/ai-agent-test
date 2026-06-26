import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from maestro.state import RunState
from maestro.run_control import RunControl, get_or_create, get, remove
from maestro.modules.module import CorrectionPrompt, Error, ErrorType, Module
from maestro.agent_loop import AgentLoop


class _WritePremise(Module):
    """One BUILD error, cleared by a single write_component call."""
    id = "writeprem"
    priority = 10

    def affected_components(self):
        return ("premise",)

    def get_errors(self, ctx):
        if ctx.artifact.get("premise"):
            return []
        return [Error(ErrorType.BUILD, "make", "premise", "write premise")]

    def get_correction_prompt(self, ctx, error):
        return CorrectionPrompt(system="s", user="u", allowed_tools=("write_component",))


def _write_call():
    return {"choices": [{"message": {"tool_calls": [
        {"id": "1", "function": {"name": "write_component",
                                 "arguments": '{"component_id": "premise", "content": {"x": 1}}'}}]}}]}


class _Conn:
    """Returns a write_component tool call; runs `on_call` once (e.g. to pause/cancel)."""
    def __init__(self, on_call=None):
        self.on_call = on_call
        self.fired = False

    def generate_with_tools(self, messages, schemas, **kw):
        if self.on_call and not self.fired:
            self.fired = True
            self.on_call()
        return _write_call()


def _tools(state):
    def write_component(component_id, content, **kw):
        state.write_component(component_id, content)
        return {"ok": True, "component_id": component_id}
    return {"write_component": write_component}


def _loop(state, control, conn):
    return AgentLoop({"frozen": True}, state, [_WritePremise()], _tools(state),
                     connector=conn, max_steps=10, on_event=lambda e: state._events.append(e),
                     control=control)


def _state(tmp_path):
    state = RunState(tmp_path)
    state._events = []
    return state


def test_cancel_before_run_writes_nothing(tmp_path):
    state = _state(tmp_path)
    control = RunControl()
    control.request_cancel()
    result = _loop(state, control, _Conn()).run()
    assert result.ok is False
    assert any(e["type"] == "build_cancelled" for e in state._events)
    assert control.status == "cancelled"
    assert state.read_component("premise") is None


def test_pause_then_resume_completes(tmp_path):
    state = _state(tmp_path)
    control = RunControl()
    conn = _Conn(on_call=control.request_pause)  # pause once, at the first decision
    loop = _loop(state, control, conn)
    t = threading.Thread(target=loop.run, daemon=True)
    t.start()

    for _ in range(200):
        if control.status == "paused":
            break
        time.sleep(0.01)
    assert control.status == "paused"
    assert any(e["type"] == "build_paused" for e in state._events)

    control.request_resume()
    t.join(timeout=5)
    assert not t.is_alive()
    types = [e["type"] for e in state._events]
    assert "build_resumed" in types
    assert state.read_component("premise") is not None


def test_registry_lifecycle():
    a = get_or_create("run-x")
    assert get("run-x") is a
    assert get_or_create("run-x") is a
    remove("run-x")
    assert get("run-x") is None
