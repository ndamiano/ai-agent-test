import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from api.routers import games
from maestro.state import RunState


def _seed_run(runs_dir, run_id, *, frozen, built, components):
    state = RunState(runs_dir / run_id)
    state.write_spec({"title": f"Game {run_id}", "frozen": frozen, "components": []})
    for cid, content in components.items():
        state.write_component(cid, content)
    if built:
        (state.run_dir / "game_output").mkdir()
    return state


def test_list_games_summarizes_and_sorts(tmp_path, monkeypatch):
    runs = tmp_path / "runs"
    monkeypatch.setattr(games, "_runs_dir", lambda: runs)

    _seed_run(runs, "old", frozen=False, built=False, components={"premise": {"q": 1}})
    _seed_run(runs, "new", frozen=True, built=True,
              components={"premise": {"q": 1}, "node_scripts": {"node_ids": []}})
    # A non-run dir without a spec is ignored.
    (runs / "junk").mkdir()

    result = asyncio.run(games.list_games())

    assert [g["run_id"] for g in result] == ["new", "old"]  # newest first by mtime
    new = next(g for g in result if g["run_id"] == "new")
    assert new == {**new, "frozen": True, "built": True, "n_components": 2,
                   "title": "Game new"}
    assert result[1]["frozen"] is False and result[1]["built"] is False


def test_list_games_empty_when_no_runs_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(games, "_runs_dir", lambda: tmp_path / "nope")
    assert asyncio.run(games.list_games()) == []


def test_get_game_404_for_unknown(tmp_path, monkeypatch):
    monkeypatch.setattr(RunState, "for_run",
                        classmethod(lambda cls, rid: RunState(tmp_path / rid)))
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        asyncio.run(games.get_game("ghost"))
    assert exc.value.status_code == 404


def test_get_game_returns_spec_artifact_todo(tmp_path, monkeypatch):
    monkeypatch.setattr(RunState, "for_run",
                        classmethod(lambda cls, rid: RunState(tmp_path / rid)))
    state = RunState(tmp_path / "g1")
    state.write_spec({"title": "G1", "frozen": True, "components": [
        {"id": "premise", "done_conditions": [
            {"type": "exists", "path": "premise.central_question"}]}]})
    state.write_component("premise", {"central_question": "Q?"})

    result = asyncio.run(games.get_game("g1"))

    assert result["frozen"] is True
    assert result["spec"]["title"] == "G1"
    assert result["artifact"]["premise"]["central_question"] == "Q?"
    assert result["todo"] == []  # the one done-condition passes
