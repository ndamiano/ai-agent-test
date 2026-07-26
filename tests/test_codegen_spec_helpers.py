"""propose_spec / amend_spec / freeze_spec — persistence + frozen flag + emitted events.

The LLM (draft_spec) and the WebSocket bus (_emit) are stubbed, so these run with no live model.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import maestro.codegen.run as run_mod
from maestro.state import RunState
from tools.execution_context import execution_context


@pytest.fixture
def events(monkeypatch):
    captured = []
    monkeypatch.setattr(run_mod, "_emit",
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
        assert RunState(run_id).read_spec() == spec
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
        assert RunState(run_id).read_spec()["frozen"] is False
    assert ("spec_amend_requested", run_id, {"note": "add a boss"}) in events


def test_freeze_spec_roundtrips_frozen_flag(tmp_path, events, stub_draft):
    with execution_context(working_directory=str(tmp_path)):
        run_id = run_mod.create_run("user1")
        run_mod.propose_spec("a maze game", run_id)

        result = run_mod.freeze_spec(run_id)

        assert result == {"ok": True, "frozen": True}
        assert RunState(run_id).read_spec()["frozen"] is True
    assert ("spec_frozen", run_id, {"title": "Stub Game"}) in events


def test_cli_draft_leaves_spec_unfrozen_for_editing(tmp_path, events, stub_draft, monkeypatch, capsys):
    """--draft stops after stage 1 so a human can edit spec.json before the build."""
    monkeypatch.setattr(run_mod.store, "list_users", lambda: [type("U", (), {"id": "u1"})()])
    monkeypatch.setattr(run_mod.db_store, "create_game", lambda *a, **k: None)
    monkeypatch.setattr(run_mod.db_store, "charge_game", lambda *a, **k: None)
    monkeypatch.setattr(run_mod.db_store, "update_spec_meta", lambda *a, **k: None)

    with execution_context(working_directory=str(tmp_path)):
        assert run_mod._cli_draft("a farm game") == 0
        run_id = capsys.readouterr().out.split("run: ")[1].split("\n")[0]
        spec = RunState(run_id).read_spec()

    assert spec["frozen"] is False
    assert spec["design"]["seen"] == "a farm game"


def test_cli_build_freezes_the_spec_on_disk(tmp_path, events, monkeypatch):
    """--build freezes whatever spec.json says now — the hand edit, not the drafted text."""
    monkeypatch.setattr(run_mod.db_store, "update_spec_meta", lambda *a, **k: None)
    monkeypatch.setattr(run_mod.db_store, "create_game", lambda *a, **k: None)
    built = []
    monkeypatch.setattr(run_mod, "run_build",
                        lambda rid, **k: built.append(rid) or run_mod.BuildResult(True, 3, 1.0, []))

    with execution_context(working_directory=str(tmp_path)):
        run_id = run_mod.create_run("u1")
        state = RunState(run_id)
        state.write_spec({"request": "r", "title": "T", "mode": "2d",
                          "design": {"controls": {"Spacebar": "jump"}}, "frozen": False})

        assert run_mod._cli_build(run_id) == 0

        spec = state.read_spec()
    assert built == [run_id]
    assert spec["frozen"] is True
    assert spec["design"]["controls"] == {" ": "jump"}     # hand-edited keys still normalize


def test_cli_build_refuses_an_unknown_run(tmp_path, capsys):
    with execution_context(working_directory=str(tmp_path)):
        assert run_mod._cli_build("nosuchrun") == 1
    assert "no spec" in capsys.readouterr().out
