"""open_ask / set_prompt — persistence, the title cut, and the emitted events.

The WebSocket bus (_emit) is stubbed. The design stage's own inference lives in test_design.py;
what is tested here is the storage either side of it.
"""

import pytest

import maestro.codegen.run as run_mod
from maestro.state import RunState


@pytest.fixture
def events(monkeypatch):
    captured = []
    monkeypatch.setattr(run_mod, "_emit",
                        lambda et, rid, **p: captured.append((et, rid, p)))
    return captured


@pytest.fixture(autouse=True)
def _no_db(monkeypatch):
    monkeypatch.setattr(run_mod.games, "create_game", lambda *a, **k: None)
    monkeypatch.setattr(run_mod.games, "update_prompt_meta", lambda *a, **k: None)
    monkeypatch.setattr(run_mod.games, "charge_game", lambda *a, **k: None)


def test_open_ask_stores_the_users_words_verbatim(tmp_runs, events):
    """The designer reads this text, and it stays on the run as the record of what was asked for,
    so nothing may rewrite it on the way in."""
    ask = "an open world RPG where combat is a card game played for ante"
    run_id = run_mod.create_run("user1")
    spec = run_mod.open_ask(run_id, ask)

    assert spec["ask"] == ask
    assert RunState(run_id).read_spec() == spec


def test_set_prompt_replaces_the_text_and_emits(tmp_runs, events):
    run_id = run_mod.create_run("user1")
    run_mod.open_ask(run_id, "a maze game")

    spec = run_mod.set_prompt(run_id, "a maze game with a boss")

    assert spec["request"] == "a maze game with a boss"
    assert RunState(run_id).read_spec()["request"] == "a maze game with a boss"
    # The title is the ask's, not the edit's: it names what the user wanted, not what they typed
    # into the box last.
    assert ("prompt_updated", run_id, {"title": "a maze game"}) in events


def test_an_empty_prompt_is_refused(tmp_runs, events):
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


def test_cli_new_stops_before_building(tmp_runs, events, monkeypatch, capsys):
    """--new leaves the prompt on disk so it can be edited before the build spends anything."""
    monkeypatch.setattr(run_mod.store, "list_users", lambda: [type("U", (), {"id": "u1"})()])
    monkeypatch.setattr(run_mod.design, "generate",
                        lambda rid, ask: run_mod.set_prompt(rid, "SYSTEMS: Farm.",
                                                            event="prompt_proposed")["request"])

    assert run_mod._cli_new("a farm game") == 0
    run_id = capsys.readouterr().out.split("run: ")[1].split("\n")[0]
    spec = RunState(run_id).read_spec()

    assert spec["request"] == "SYSTEMS: Farm." and spec["ask"] == "a farm game"


def test_cli_build_sends_whatever_the_prompt_says_now(tmp_runs, events, monkeypatch):
    """--build reads spec.json at build time, so a hand edit is what runs."""
    built = []
    monkeypatch.setattr(run_mod, "run_build",
                        lambda rid, **k: built.append(rid) or run_mod.BuildResult(True, 3, 1.0))

    run_id = run_mod.create_run("u1")
    state = RunState(run_id)
    state.write_spec({"request": "the hand-edited text", "title": "T"})

    assert run_mod._cli_build(run_id) == 0
    assert state.read_spec()["request"] == "the hand-edited text"
    assert built == [run_id]


def test_cli_build_refuses_an_unknown_run(tmp_runs, capsys):
    assert run_mod._cli_build("nosuchrun") == 1
    assert "no prompt" in capsys.readouterr().out
