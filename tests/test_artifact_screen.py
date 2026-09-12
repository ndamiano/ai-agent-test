"""The artifact text gate: the finished game's own text is screened at finalize, and a hit HOLDS
the build — no staging, no snapshot — with a violation row for the admin panel. Same narrow screen
as every other seam: synthetic CSAM-combination fixtures only, mature themes pass."""

import pytest

from db import events
from maestro.codegen import build_state
from maestro.codegen.artifact_screen import screen_artifact
from maestro.codegen.build_state import BuildCursor

FLAGGED = "dialogue: a nude schoolgirl appears"


def _game(tmp_path, files: dict):
    d = tmp_path / "game"
    d.mkdir(exist_ok=True)
    for name, text in files.items():
        p = d / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    return d


def test_a_clean_game_passes(tmp_path):
    _game(tmp_path, {"index.html": "<html>a dark revenge story</html>",
                     "main.js": "const hero = 'grizzled'"})
    assert screen_artifact(tmp_path) is None


def test_a_hit_names_the_file_and_the_violation(tmp_path):
    _game(tmp_path, {"index.html": "<html></html>", "story.js": FLAGGED})
    path, violation = screen_artifact(tmp_path)
    assert path == "story.js"
    assert violation.category == "csam_combination"


def test_the_combination_is_per_file_not_across_files(tmp_path):
    """One half per file is the innocent case — a kids' game with an unrelated adult reference
    elsewhere must not combine across file boundaries."""
    _game(tmp_path, {"a.js": "a schoolgirl walks to class", "b.js": "a steamy nude painting"})
    assert screen_artifact(tmp_path) is None


def test_the_vendored_renderer_is_not_the_games_text(tmp_path):
    _game(tmp_path, {"three.module.js": FLAGGED})
    assert screen_artifact(tmp_path) is None


def test_binary_suffixes_are_not_read(tmp_path):
    d = _game(tmp_path, {"index.html": "<html></html>"})
    (d / "assets").mkdir()
    (d / "assets" / "sprite.webp").write_bytes(FLAGGED.encode())
    assert screen_artifact(tmp_path) is None


def test_a_missing_game_folder_passes(tmp_path):
    assert screen_artifact(tmp_path) is None


# ── The held path: what a hit does to the finalize ───────────────────────────────────────────


@pytest.fixture
def finalize_env(monkeypatch, tmp_path):
    from maestro.codegen import build_chain

    seen = {"status": None, "attempt": None, "staged": False, "snapped": False}
    monkeypatch.setattr(build_chain, "_emit", lambda *a, **k: None)
    monkeypatch.setattr(build_chain.games, "set_status",
                        lambda rid, s: seen.__setitem__("status", s))
    monkeypatch.setattr(build_chain.games, "build_finished",
                        lambda bid, s, steps=None: seen.__setitem__("attempt", s))
    monkeypatch.setattr(build_chain.jobs, "abandon_build_jobs", lambda bid, err: 1)
    monkeypatch.setattr(build_chain, "stage_for_play",
                        lambda *a, **k: seen.__setitem__("staged", True))
    monkeypatch.setattr(build_chain.snapshots, "take",
                        lambda *a, **k: seen.__setitem__("snapped", True))
    build_state.save(tmp_path, BuildCursor(build_id="b1", step=5))
    return build_chain, seen


def test_flagged_text_holds_the_build(finalize_env, tmp_path):
    """A playable game whose text trips the screen never reaches /play: status `held`, nothing
    staged, nothing snapshotted, and the cursor does not claim ok."""
    build_chain, seen = finalize_env
    _game(tmp_path, {"index.html": f"<html>{FLAGGED}</html>"})
    build_chain.stop(str(tmp_path))
    assert seen["status"] == "held"
    assert seen["attempt"] == "held"
    assert seen["staged"] is False and seen["snapped"] is False
    assert build_state.load(tmp_path).ok is False


def test_a_held_finalize_records_the_violation(finalize_env, tmp_path):
    build_chain, _ = finalize_env
    _game(tmp_path, {"index.html": f"<html>{FLAGGED}</html>"})
    build_chain.stop(str(tmp_path))
    rows = events.list_violations()
    assert rows and rows[0]["source"] == "artifact:index.html"
    assert rows[0]["category"] == "csam_combination"


def test_a_clean_finalize_stages_as_before(finalize_env, tmp_path):
    build_chain, seen = finalize_env
    _game(tmp_path, {"index.html": "<html>a space pirate game</html>"})
    build_chain.stop(str(tmp_path))
    assert seen["status"] == "built" and seen["staged"] is True
