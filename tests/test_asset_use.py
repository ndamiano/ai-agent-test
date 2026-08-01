"""Does the game load the art it asked for — static analysis over the game's own source.

Both halves are BROKEN, not bad: an asset nobody loads was paid for and will never be seen, and a
path with no file behind it is a broken image in the shipped game. Neither is an opinion about the
picture, and nothing here runs a model or touches a GPU.

Measured over the staged games this was written against: one build asked for 11 assets, rendered
all 11 and referenced none; another rendered 67, used none, and shipped 114 paths under a folder
that does not exist.
"""

import json

import pytest

import maestro.state
from maestro.codegen import asset_use
from maestro.state import RunState

RUN = "r1"


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    monkeypatch.setattr(maestro.state, "resolve_base_path", lambda input_path=None: tmp_path)


@pytest.fixture
def game(tmp_path):
    d = RunState(RUN).run_dir / "game"
    (d / "assets").mkdir(parents=True, exist_ok=True)
    return d


def _manifest(game, *entries):
    (game / "assets.json").write_text(json.dumps({"images": [
        {"id": e, "file": f"assets/{e}.png", "prompt": "art"} for e in entries]}))


def _audit(game):
    return asset_use.audit(RunState(RUN).run_dir)


def test_a_wired_up_game_reports_nothing(game):
    _manifest(game, "hero")
    (game / "assets" / "hero.png").write_bytes(b"x")
    (game / "game.js").write_text("const img = new Image(); img.src = 'assets/hero.png'")

    assert _audit(game) == {"unreferenced": [], "missing": []}
    assert asset_use.report(_audit(game)) == ""


def test_art_the_game_never_loads_is_named(game):
    """The ghost game: every asset asked for up front, the whole game then drawn with fillRect."""
    _manifest(game, "ghost", "floor")
    (game / "game.js").write_text("ctx.fillRect(0, 0, 32, 32)")

    assert _audit(game)["unreferenced"] == ["floor", "ghost"]


def test_a_path_with_no_file_behind_it_is_named(game):
    _manifest(game, "hero")
    (game / "assets" / "hero.png").write_bytes(b"x")
    (game / "game.js").write_text("load('assets/hero.png'); load('assets/cards/dragon.png')")

    assert _audit(game)["missing"] == ["assets/cards/dragon.png"]


def test_art_still_rendering_is_neither(game):
    """It is in the manifest, so the source naming it is right and the file arriving late is the
    asset stage working. Only a path in neither the manifest nor the folder is missing."""
    _manifest(game, "hero")
    (game / "game.js").write_text("load('assets/hero.png')")

    assert _audit(game) == {"unreferenced": [], "missing": []}


def test_an_id_named_anywhere_counts_as_loaded(game):
    """A game that assembles "assets/" + id + ".png" at runtime still names the id in its data."""
    _manifest(game, "hero")
    (game / "assets" / "hero.png").write_bytes(b"x")
    (game / "game.js").write_text(
        "const SPRITES = ['hero']\nconst url = 'assets/' + SPRITES[0] + '.png'")

    assert _audit(game)["unreferenced"] == []


def test_the_vendored_renderer_is_not_the_game_source(game):
    """three.js and GLTFLoader talk about `assets/` paths in their own comments — reading them as
    the game's source invents missing files in every 3D build."""
    _manifest(game, "hero")
    (game / "assets" / "hero.png").write_bytes(b"x")
    (game / "GLTFLoader.js").write_text("// url = 'assets/models/model.gltf'")
    (game / "game.js").write_text("load('assets/hero.png')")

    assert _audit(game)["missing"] == []


def test_a_game_that_asked_for_nothing_reports_nothing(game):
    (game / "game.js").write_text("ctx.fillRect(0, 0, 32, 32)")
    assert _audit(game) == {"unreferenced": [], "missing": []}


def test_the_report_says_both_ways_out(game):
    """A build told only that something is wrong contorts the game guessing which way out is meant."""
    _manifest(game, "ghost")
    (game / "game.js").write_text("load('assets/cards/dragon.png')")

    text = asset_use.report(_audit(game))
    assert "ghost" in text and "assets/cards/dragon.png" in text
    assert "generate_media" in text and "stop loading it" in text


def test_a_long_list_is_clipped_and_says_so(game):
    _manifest(game, *[f"card{i}" for i in range(30)])
    (game / "game.js").write_text("ctx.fillRect(0, 0, 32, 32)")

    text = asset_use.report(_audit(game), limit=5)
    assert "and 25 more" in text
