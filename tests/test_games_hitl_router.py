import asyncio
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from api.routers import games
from maestro.state import RunState
from maestro import run_control


def _patch(monkeypatch, base):
    monkeypatch.setattr(RunState, "for_run",
                        classmethod(lambda cls, rid: RunState(base / rid)))


def _frozen_run(base, run_id="g", modules=None, params=None):
    state = RunState(base / run_id)
    state.write_spec({"title": "G", "frozen": True, "modules": modules or [], "params": params or {}})
    return state


# ── control endpoints ─────────────────────────────────────────────────────────
def test_control_409_when_no_build(tmp_path, monkeypatch):
    _patch(monkeypatch, tmp_path)
    run_control.remove("g")
    for fn in (games.pause_game, games.resume_game, games.cancel_game):
        with pytest.raises(HTTPException) as exc:
            asyncio.run(fn("g"))
        assert exc.value.status_code == 409


def test_pause_resume_cancel_flip_control(tmp_path, monkeypatch):
    _patch(monkeypatch, tmp_path)
    ctrl = run_control.get_or_create("g")
    try:
        asyncio.run(games.pause_game("g"))
        assert ctrl.paused is True
        asyncio.run(games.resume_game("g"))
        assert ctrl.paused is False
        asyncio.run(games.cancel_game("g"))
        assert ctrl.cancelled is True
    finally:
        run_control.remove("g")


# ── todos + waivers ────────────────────────────────────────────────────────────
def test_todo_endpoints(tmp_path, monkeypatch):
    _patch(monkeypatch, tmp_path)
    state = _frozen_run(tmp_path)

    todo = asyncio.run(games.add_todo_game("g", games.TodoBody(component_id="premise", text="fix it")))
    assert todo["component_id"] == "premise" and todo["done"] is False
    assert state.read_human_todos()[0]["text"] == "fix it"

    asyncio.run(games.resolve_todo_game("g", todo["id"], games.ResolveBody(done=True)))
    assert state.read_human_todos()[0]["done"] is True

    with pytest.raises(HTTPException) as exc:
        asyncio.run(games.resolve_todo_game("g", "ghost", games.ResolveBody()))
    assert exc.value.status_code == 404


def test_waive_endpoints(tmp_path, monkeypatch):
    _patch(monkeypatch, tmp_path)
    state = _frozen_run(tmp_path, modules=["cast"])    # empty premise -> cast emits real errors
    from maestro.modules.human import effective_failures

    idkey = effective_failures(state.read_spec(), state)[0]["idkey"]
    asyncio.run(games.waive_game("g", games.WaiveBody(idkey=idkey)))
    assert state.read_waivers()[0]["idkey"] == idkey

    asyncio.run(games.unwaive_game("g", games.UnwaiveBody(idkey=idkey)))
    assert state.read_waivers() == []

    with pytest.raises(HTTPException) as exc:
        asyncio.run(games.unwaive_game("g", games.UnwaiveBody(idkey="nope")))
    assert exc.value.status_code == 404


def test_detail_surfaces_human_todos_and_status(tmp_path, monkeypatch):
    _patch(monkeypatch, tmp_path)
    _frozen_run(tmp_path, modules=["cast"])   # empty premise -> cast BUILD errors
    asyncio.run(games.add_todo_game("g", games.TodoBody(component_id="premise", text="x")))

    detail = asyncio.run(games.get_game("g"))
    assert detail["status"] == "idle"
    assert len(detail["human_todos"]) == 1
    # the open premise (build) checks + the human todo both show in the effective to-do
    assert {t["type"] for t in detail["todo"]} == {"build", "human"}


# ── edit + regenerate + compile gating ──────────────────────────────────────────
def test_edit_component_writes_when_idle(tmp_path, monkeypatch):
    _patch(monkeypatch, tmp_path)
    state = _frozen_run(tmp_path)
    res = asyncio.run(games.edit_component_game(
        "g", "notes", games.ComponentBody(content={"text": "hand-written"})))
    assert res["ok"] is True
    assert state.read_component("notes") == {"text": "hand-written"}


def test_edit_blocked_while_building_unpaused(tmp_path, monkeypatch):
    _patch(monkeypatch, tmp_path)
    _frozen_run(tmp_path)
    games._active_builds.add("g")
    run_control.get_or_create("g")  # status defaults to "running"
    try:
        with pytest.raises(HTTPException) as exc:
            asyncio.run(games.edit_component_game(
                "g", "notes", games.ComponentBody(content={"x": 1})))
        assert exc.value.status_code == 409
    finally:
        games._active_builds.discard("g")
        run_control.remove("g")


def test_edit_allowed_while_paused(tmp_path, monkeypatch):
    _patch(monkeypatch, tmp_path)
    state = _frozen_run(tmp_path)
    games._active_builds.add("g")
    ctrl = run_control.get_or_create("g")
    ctrl.set_status("paused")
    try:
        res = asyncio.run(games.edit_component_game(
            "g", "notes", games.ComponentBody(content={"x": 1})))
        assert res["ok"] is True
        assert state.read_component("notes") == {"x": 1}
    finally:
        games._active_builds.discard("g")
        run_control.remove("g")


def test_edit_node_patches_line(tmp_path, monkeypatch):
    _patch(monkeypatch, tmp_path)
    state = _frozen_run(tmp_path)
    state.write_component("nodes", {"node_ids": ["n1"], "nodes": {
        "n1": {"lines": [{"speaker": "a", "text": "old"}], "end": {"type": "end"}}}})

    res = asyncio.run(games.edit_node_game("g", "n1", games.NodeEditBody(line_index=0, text="new")))
    assert res["ok"] is True
    assert state.read_component("nodes")["nodes"]["n1"]["lines"][0]["text"] == "new"


def test_compile_dispatches_to_engine(tmp_path, monkeypatch):
    _patch(monkeypatch, tmp_path)
    _frozen_run(tmp_path)
    calls = {}

    def fake_compile_for(engine):
        def _c(run_dir, distribute=False):
            calls["engine"] = engine
            calls["distribute"] = distribute
            return {"ok": True, "reason": None}
        return _c

    monkeypatch.setattr("maestro.engines.compile_for", fake_compile_for)
    res = asyncio.run(games.compile_game("g", games.CompileBody(distribute=True)))
    assert res["ok"] is True
    assert calls == {"engine": "renpy", "distribute": True}
