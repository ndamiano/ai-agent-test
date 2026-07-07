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


def _patch_for_run(monkeypatch, base):
    monkeypatch.setattr(RunState, "for_run",
                        classmethod(lambda cls, rid: RunState(base / rid)))


def test_freeze_game_sets_frozen(tmp_path, monkeypatch):
    _patch_for_run(monkeypatch, tmp_path)
    RunState(tmp_path / "g").write_spec({"title": "G", "frozen": False, "components": []})

    assert asyncio.run(games.freeze_game("g"))["frozen"] is True
    assert RunState(tmp_path / "g").read_spec()["frozen"] is True


def test_freeze_game_404(tmp_path, monkeypatch):
    _patch_for_run(monkeypatch, tmp_path)
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        asyncio.run(games.freeze_game("ghost"))
    assert exc.value.status_code == 404


def test_download_serves_godot_self_contained_zip(tmp_path, monkeypatch):
    _patch_for_run(monkeypatch, tmp_path)
    state = RunState(tmp_path / "g")
    state.write_spec({"title": "G", "frozen": True, "components": []})
    zip_path = state.run_dir / "godot_dist.zip"
    zip_path.write_bytes(b"PK\x03\x04 fake zip")

    result = asyncio.run(games.download_game("g"))
    assert Path(result.path) == zip_path
    assert result.media_type == "application/zip"


def test_download_zips_renpy_dist_dir(tmp_path, monkeypatch):
    _patch_for_run(monkeypatch, tmp_path)
    state = RunState(tmp_path / "g")
    state.write_spec({"title": "G", "frozen": True, "components": []})
    dist = state.run_dir / "dist"
    dist.mkdir()
    (dist / "MyGame-1.0-pc.zip").write_bytes(b"platform build")

    result = asyncio.run(games.download_game("g"))
    assert Path(result.path) == state.run_dir / "download.zip"
    assert Path(result.path).exists()


def test_download_409_when_not_packaged(tmp_path, monkeypatch):
    _patch_for_run(monkeypatch, tmp_path)
    RunState(tmp_path / "g").write_spec({"title": "G", "frozen": True, "components": []})
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        asyncio.run(games.download_game("g"))
    assert exc.value.status_code == 409


def test_download_404_for_unknown(tmp_path, monkeypatch):
    _patch_for_run(monkeypatch, tmp_path)
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        asyncio.run(games.download_game("ghost"))
    assert exc.value.status_code == 404


def test_build_requires_frozen_spec(tmp_path, monkeypatch):
    _patch_for_run(monkeypatch, tmp_path)
    RunState(tmp_path / "g").write_spec({"title": "G", "frozen": False, "components": []})
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        asyncio.run(games.build_game("g"))
    assert exc.value.status_code == 400


def test_build_starts_thread_and_guards_double_build(tmp_path, monkeypatch):
    import threading
    _patch_for_run(monkeypatch, tmp_path)
    RunState(tmp_path / "g").write_spec({"title": "G", "frozen": True, "components": []})

    release = threading.Event()
    started = threading.Event()

    def fake_run_build(run_id, *a, **k):
        started.set()
        release.wait(timeout=5)

    monkeypatch.setattr("maestro.run.run_build", fake_run_build)

    try:
        assert asyncio.run(games.build_game("g"))["status"] == "building"
        assert started.wait(timeout=5)
        # Second build while the first is in flight is rejected.
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc:
            asyncio.run(games.build_game("g"))
        assert exc.value.status_code == 409
    finally:
        release.set()
    # After the thread finishes, the guard clears.
    for _ in range(50):
        if "g" not in games._active_builds:
            break
        import time; time.sleep(0.05)
    assert "g" not in games._active_builds


def test_amend_spec_reresolves_modules_and_unfreezes(tmp_path, monkeypatch):
    _patch_for_run(monkeypatch, tmp_path)
    RunState(tmp_path / "g").write_spec(
        {"title": "G", "frozen": True, "modules": ["scenes"], "params": {}})

    body = games.AmendBody(changes={"modules": ["world", "combat"]},
                           reason="switch to a fighting point-and-click")
    result = asyncio.run(games.amend_game_spec_route("g", body))

    mods = result["spec"]["modules"]
    assert "world" in mods and "combat" in mods              # combat pulls world+scenes
    assert result["spec"]["engine"] == "godot"               # combat only plays on godot
    assert result["frozen"] is False                         # amend always un-freezes for re-approval
    assert set(result["spec"]["module_reasons"]) == set(mods)


def test_amend_spec_404_for_unknown(tmp_path, monkeypatch):
    _patch_for_run(monkeypatch, tmp_path)
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        asyncio.run(games.amend_game_spec_route("ghost", games.AmendBody(changes={}, reason="x")))
    assert exc.value.status_code == 404


def test_get_game_returns_spec_artifact_todo(tmp_path, monkeypatch):
    monkeypatch.setattr(RunState, "for_run",
                        classmethod(lambda cls, rid: RunState(tmp_path / rid)))
    state = RunState(tmp_path / "g1")
    # no modules -> no checks, so the to-do is empty once the spec is frozen
    state.write_spec({"title": "G1", "frozen": True, "modules": [], "params": {}})
    state.write_component("premise", {"central_question": "Q?"})

    result = asyncio.run(games.get_game("g1"))

    assert result["frozen"] is True
    assert result["spec"]["title"] == "G1"
    assert result["artifact"]["premise"]["central_question"] == "Q?"
    assert result["todo"] == []  # no modules -> nothing to do
