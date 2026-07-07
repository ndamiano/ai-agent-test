import asyncio
import json
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from maestro.state import RunState
from maestro.run_control import RunControl
from maestro.tools import build_tools
from maestro.rewrite import rewrite_node
from maestro.agent_loop import AgentLoop
from api.routers import games
from maestro import run_control
from auth.store import User

U = User(id="u1", handle="alice", role="user")


def _spec():
    # unconstrained: the rewrite path forces write_node, so no VN location/min-lines floor here
    return {"title": "T", "frozen": True, "modules": [], "params": {}}


_NODE = {"lines": [{"speaker": "a", "text": "old line one"}, {"speaker": "b", "text": "old line two"}],
         "end": {"type": "end"}}


def _seed_nodes(state):
    state.write_component("nodes", {"node_ids": ["n1"], "nodes": {"n1": dict(_NODE)}})


# ── edit_node full-content replace (manual edit path) ───────────────────────────
def test_edit_node_replaces_whole_content(tmp_path):
    state = RunState(tmp_path)
    state.write_spec({"frozen": True, "components": []})
    _seed_nodes(state)
    tools = build_tools(_spec(), state)

    new = {"lines": [{"speaker": "a", "text": "brand new"}], "end": {"type": "end"}}
    res = tools["edit_node"]("n1", content=new, force=True)
    assert res["ok"] is True
    assert state.read_component("nodes")["nodes"]["n1"] == new


def test_edit_node_rejects_bad_content(tmp_path):
    state = RunState(tmp_path)
    state.write_spec({"frozen": True, "components": []})
    _seed_nodes(state)
    tools = build_tools(_spec(), state)

    res = tools["edit_node"]("n1", content={"lines": [], "end": {"type": "end"}}, force=True)
    assert res["ok"] is False and "non-empty" in res["error"]


# ── auto-pause after a component finishes ───────────────────────────────────────
def test_milestone_requests_pause_when_armed(tmp_path):
    state = RunState(tmp_path)
    control = RunControl()
    control.set_auto_pause(True)
    events = []
    loop = AgentLoop(_spec(), state, [], {}, connector=object(), control=control,
                     on_event=events.append)

    loop._fire_milestone("premise")
    assert control.paused is True
    assert any(e["type"] == "auto_paused" and e["component_id"] == "premise" for e in events)


def test_milestone_no_pause_when_disarmed(tmp_path):
    control = RunControl()
    loop = AgentLoop(_spec(), RunState(tmp_path), [], {}, connector=object(), control=control)
    loop._fire_milestone("premise")
    assert control.paused is False


# ── single-node rewrite (LLM driven, fake connector) ────────────────────────────
class _FakeConn:
    def __init__(self, content):
        self.content = content
        self.calls = 0

    def generate_with_tools(self, messages, schemas, **kw):
        self.calls += 1
        # The note must reach the model — assert it's in the rendered conversation.
        assert any("make it tenser" in (m.get("content") or "") for m in messages)
        return {"choices": [{"message": {"tool_calls": [{
            "id": "1",
            "function": {"name": "write_node",
                         "arguments": json.dumps({"node_id": "n1", "content": self.content})},
        }]}}]}


def test_rewrite_node_replaces_via_llm(tmp_path):
    state = RunState(tmp_path)
    state.write_spec({"frozen": True, "components": []})
    _seed_nodes(state)
    spec = _spec()
    tools = build_tools(spec, state)
    rewritten = {"lines": [{"speaker": "a", "text": "a far tenser beat"},
                           {"speaker": "b", "text": "the air goes cold"}],
                 "end": {"type": "end"}}
    conn = _FakeConn(rewritten)

    res = rewrite_node(spec, state, "n1", "make it tenser", tools, connector=conn)
    assert res["ok"] is True
    assert state.read_component("nodes")["nodes"]["n1"] == rewritten


def test_rewrite_node_missing_node(tmp_path):
    state = RunState(tmp_path)
    state.write_spec({"frozen": True, "components": []})
    _seed_nodes(state)
    spec = _spec()
    res = rewrite_node(spec, state, "ghost", "x", build_tools(spec, state), connector=_FakeConn({}))
    assert res["ok"] is False and "ghost" in res["error"]


# ── router wiring ──────────────────────────────────────────────────────────────
def _patch(monkeypatch, base):
    monkeypatch.setattr(RunState, "for_run",
                        classmethod(lambda cls, rid: RunState(base / rid)))


def test_auto_pause_toggle_requires_build(tmp_path, monkeypatch):
    _patch(monkeypatch, tmp_path)
    state = RunState(tmp_path / "g")
    state.write_spec({"title": "G", "frozen": True, "components": []})
    state.write_owner("u1")
    run_control.remove("g")
    with pytest.raises(HTTPException) as exc:
        asyncio.run(games.auto_pause_game("g", games.AutoPauseBody(enabled=True), user=U))
    assert exc.value.status_code == 409

    ctrl = run_control.get_or_create("g")
    try:
        asyncio.run(games.auto_pause_game("g", games.AutoPauseBody(enabled=True), user=U))
        assert ctrl.auto_pause is True
    finally:
        run_control.remove("g")


def test_rewrite_endpoint_kicks_thread(tmp_path, monkeypatch):
    import threading
    _patch(monkeypatch, tmp_path)
    state = RunState(tmp_path / "g")
    state.write_spec({"title": "G", "frozen": True, "components": []})
    state.write_owner("u1")
    _seed_nodes(state)

    done = threading.Event()
    monkeypatch.setattr("maestro.run.rewrite_node_run",
                        lambda rid, nid, note: done.set())

    res = asyncio.run(games.rewrite_node_game("g", "n1", games.RewriteBody(note="tenser"), user=U))
    assert res["status"] == "rewriting"
    assert done.wait(timeout=5)
    for _ in range(50):
        if "g/n1" not in games._rewriting:
            break
        import time; time.sleep(0.02)
    assert "g/n1" not in games._rewriting


def test_node_content_edit_endpoint(tmp_path, monkeypatch):
    _patch(monkeypatch, tmp_path)
    state = RunState(tmp_path / "g")
    state.write_spec({"title": "G", "frozen": True, "components": []})
    state.write_owner("u1")
    _seed_nodes(state)

    new = {"lines": [{"speaker": "a", "text": "hand edited"}], "end": {"type": "end"}}
    res = asyncio.run(games.edit_node_game("g", "n1", games.NodeEditBody(content=new), user=U))
    assert res["ok"] is True
    assert state.read_component("nodes")["nodes"]["n1"] == new
