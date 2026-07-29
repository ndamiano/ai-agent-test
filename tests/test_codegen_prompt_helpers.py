"""propose_prompt / set_prompt — persistence, the title cut, and the emitted events.

The WebSocket bus (_emit) is stubbed, so these run with no live model. Nothing here needs one:
the prompt stage runs no inference at all.
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


@pytest.fixture(autouse=True)
def _no_db(monkeypatch):
    monkeypatch.setattr(run_mod.db_store, "create_game", lambda *a, **k: None)
    monkeypatch.setattr(run_mod.db_store, "update_prompt_meta", lambda *a, **k: None)
    monkeypatch.setattr(run_mod.db_store, "charge_game", lambda *a, **k: None)


def test_propose_prompt_stores_the_request_verbatim(tmp_path, events):
    """The build reads this text as its user message, so nothing may rewrite it on the way in."""
    request = "an open world RPG where combat is a card game played for ante"
    with execution_context(working_directory=str(tmp_path)):
        run_id = run_mod.create_run("user1")
        spec = run_mod.propose_prompt(request, run_id)

        assert spec["request"] == request
        assert RunState(run_id).read_spec() == spec
    assert ("prompt_proposed", run_id, {"title": spec["title"]}) in events


def test_set_prompt_replaces_the_text_and_emits(tmp_path, events):
    with execution_context(working_directory=str(tmp_path)):
        run_id = run_mod.create_run("user1")
        run_mod.propose_prompt("a maze game", run_id)

        spec = run_mod.set_prompt(run_id, "a maze game with a boss")

        assert spec["request"] == "a maze game with a boss"
        assert RunState(run_id).read_spec()["request"] == "a maze game with a boss"
    assert ("prompt_updated", run_id, {"title": "a maze game with a boss"}) in events


def test_an_empty_prompt_is_refused(tmp_path, events):
    with execution_context(working_directory=str(tmp_path)):
        run_id = run_mod.create_run("user1")
        with pytest.raises(ValueError):
            run_mod.set_prompt(run_id, "   \n  ")


@pytest.mark.parametrize("text, expected", [
    ("pong", "pong"),
    ("a game about\nsecond line", "a game about"),
    ("x" * 40 + " " + "y" * 40, "x" * 40 + "…"),
])
def test_title_is_cut_from_the_first_line(text, expected):
    assert run_mod._title_of(text) == expected


def test_cli_new_stops_before_building(tmp_path, events, monkeypatch, capsys):
    """--new leaves the prompt on disk so it can be edited before the build spends anything."""
    monkeypatch.setattr(run_mod.store, "list_users", lambda: [type("U", (), {"id": "u1"})()])

    with execution_context(working_directory=str(tmp_path)):
        assert run_mod._cli_new("a farm game") == 0
        run_id = capsys.readouterr().out.split("run: ")[1].split("\n")[0]
        spec = RunState(run_id).read_spec()

    assert spec["request"] == "a farm game"


def test_cli_build_sends_whatever_the_prompt_says_now(tmp_path, events, monkeypatch):
    """--build reads spec.json at build time, so a hand edit is what runs."""
    built = []
    monkeypatch.setattr(run_mod, "run_build",
                        lambda rid, **k: built.append(rid) or run_mod.BuildResult(True, 3, 1.0))

    with execution_context(working_directory=str(tmp_path)):
        run_id = run_mod.create_run("u1")
        state = RunState(run_id)
        state.write_spec({"request": "the hand-edited text", "title": "T"})

        assert run_mod._cli_build(run_id) == 0
        assert state.read_spec()["request"] == "the hand-edited text"
    assert built == [run_id]


def test_cli_build_refuses_an_unknown_run(tmp_path, capsys):
    with execution_context(working_directory=str(tmp_path)):
        assert run_mod._cli_build("nosuchrun") == 1
    assert "no prompt" in capsys.readouterr().out
