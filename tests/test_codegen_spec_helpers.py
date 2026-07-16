"""propose_spec / amend_spec / freeze_spec — persistence + frozen flag + emitted events.

The LLM (draft_spec) and the WebSocket bus (_emit) are stubbed, so these run with no live model.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from tools.execution_context import execution_context
from maestro.state import RunState
import maestro.codegen.run as run_mod
import tools.build_events as build_events


@pytest.fixture
def events(monkeypatch):
    captured = []
    monkeypatch.setattr(build_events, "_emit",
                        lambda et, rid, **p: captured.append((et, rid, p)))
    return captured


@pytest.fixture
def stub_draft(monkeypatch):
    def fake(request):
        return {"request": request, "title": "Stub Game", "mode": "2d",
                "design": {"title": "Stub Game", "mode": "2d", "seen": request}, "frozen": False}
    monkeypatch.setattr(run_mod, "draft_spec", fake)


def test_propose_spec_persists_and_emits(tmp_path, events, stub_draft):
    with execution_context(working_directory=str(tmp_path)):
        run_id = run_mod.create_run("user1")
        spec = run_mod.propose_spec("a maze game", run_id)

        assert spec["title"] == "Stub Game"
        assert RunState.for_run(run_id).read_spec() == spec
        assert spec["frozen"] is False
    assert ("spec_proposed", run_id, {"title": "Stub Game", "mode": "2d"}) in events


def test_amend_spec_rewrites_unfrozen_and_emits(tmp_path, events, stub_draft):
    with execution_context(working_directory=str(tmp_path)):
        run_id = run_mod.create_run("user1")
        run_mod.propose_spec("a maze game", run_id)
        run_mod.freeze_spec(run_id)

        amended = run_mod.amend_spec(run_id, "add a boss")

        assert amended["frozen"] is False
        assert amended["request"] == "a maze game"       # original request preserved
        assert "add a boss" in amended["design"]["seen"]  # note reached the draft call
        assert RunState.for_run(run_id).read_spec()["frozen"] is False
    assert ("spec_amend_requested", run_id, {"note": "add a boss"}) in events


def test_freeze_spec_roundtrips_frozen_flag(tmp_path, events, stub_draft):
    with execution_context(working_directory=str(tmp_path)):
        run_id = run_mod.create_run("user1")
        run_mod.propose_spec("a maze game", run_id)

        result = run_mod.freeze_spec(run_id)

        assert result == {"ok": True, "frozen": True}
        assert RunState.for_run(run_id).read_spec()["frozen"] is True
    assert ("spec_frozen", run_id, {"title": "Stub Game"}) in events
