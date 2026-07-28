"""The five tools (maestro/codegen/tools.py) — deliberately the smallest surface that works."""
import pytest

from maestro.codegen.tools import MAX_READ_CHARS, build_tools
from maestro.state import RunState

SRC = "const a = 1;\nconst b = 2;\nconst a2 = 1;\n"


def _game(tmp_path, files):
    d = tmp_path / "game"
    d.mkdir(exist_ok=True)
    for name, body in files.items():
        p = d / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")


@pytest.fixture
def tools(tmp_path):
    _game(tmp_path, {"game.js": SRC})
    return build_tools(RunState(tmp_path))


# ── the path guard (the one thing that is never relaxed) ──────────────────────
@pytest.mark.parametrize("path", ["../escaped.js", "../../etc/passwd", "a/../../out.js"])
def test_a_path_cannot_escape_the_game_folder(tools, tmp_path, path):
    assert tools["write_file"](path=path, content="x")["ok"] is False
    assert not (tmp_path.parent / "escaped.js").exists()


def test_nested_paths_are_allowed(tools, tmp_path):
    assert tools["write_file"](path="assets/cards.json", content="[]")["ok"] is True
    assert (tmp_path / "game" / "assets" / "cards.json").read_text() == "[]"


# ── write_file ────────────────────────────────────────────────────────────────
def test_write_overwrites(tools, tmp_path):
    tools["write_file"](path="game.js", content="const x = 1;\n")
    assert (tmp_path / "game" / "game.js").read_text() == "const x = 1;\n"


# ── edit_file ─────────────────────────────────────────────────────────────────
def test_edit_replaces_a_unique_snippet(tools, tmp_path):
    r = tools["edit_file"](path="game.js", old_text="const b = 2;", new_text="const b = 3;")
    assert r["ok"] is True
    assert "const b = 3;" in (tmp_path / "game" / "game.js").read_text()


def test_edit_needs_no_prior_read(tools):
    """The grid harness had no read-before-edit rule; adding one only invents a failure mode."""
    assert tools["edit_file"](path="game.js", old_text="const b = 2;", new_text="x")["ok"] is True


def test_edit_refuses_an_ambiguous_snippet(tools, tmp_path):
    r = tools["edit_file"](path="game.js", old_text="const a", new_text="const z")
    assert r["ok"] is False and "appears 2 times" in r["error"]
    assert (tmp_path / "game" / "game.js").read_text() == SRC


def test_edit_refuses_a_missing_snippet(tools, tmp_path):
    r = tools["edit_file"](path="game.js", old_text="nope", new_text="x")
    assert r["ok"] is False and "not found" in r["error"]
    assert (tmp_path / "game" / "game.js").read_text() == SRC


def test_edit_on_a_missing_file_is_an_error(tools):
    assert tools["edit_file"](path="nope.js", old_text="a", new_text="b")["ok"] is False


# ── read_file / list_files ────────────────────────────────────────────────────
def test_read_returns_the_whole_file(tools):
    assert tools["read_file"](path="game.js")["content"] == SRC


def test_read_truncates_only_past_the_ceiling(tmp_path):
    _game(tmp_path, {"big.js": "x" * (MAX_READ_CHARS + 500)})
    r = build_tools(RunState(tmp_path))["read_file"](path="big.js")
    assert r["ok"] and "truncated" in r["content"]
    assert len(r["content"]) < MAX_READ_CHARS + 200


def test_a_long_single_line_is_not_elided_below_the_ceiling(tmp_path):
    """Mid-file elision broke edit anchors; the harness only ever truncated the tail."""
    line = "const DATA = [" + ",".join(str(i) for i in range(2000)) + "];\n"
    _game(tmp_path, {"data.js": line})
    r = build_tools(RunState(tmp_path))["read_file"](path="data.js")
    assert r["content"] == line


def test_read_missing_file_is_an_error(tools):
    assert tools["read_file"](path="nope.js")["ok"] is False


def test_list_files_skips_scratch(tools, tmp_path):
    (tmp_path / "game" / "_transcript.jsonl").write_text("x")
    assert [f["path"] for f in tools["list_files"]()["files"]] == ["game.js"]


# ── a missing required argument is an error, never a default ─────────────────
def test_write_without_a_path_is_an_error_not_a_default(tools, tmp_path):
    """THE regression. Defaulting a missing `path` to index.html meant every write in a run landed
    on the same file and the last one — the game's JavaScript — won, so index.html held no HTML and
    the page rendered its own source. Told the argument is missing, the model resends correctly."""
    r = tools["write_file"](content="const x = 1;")
    assert r["ok"] is False and "path" in r["error"]
    assert not (tmp_path / "game" / "index.html").exists()


@pytest.mark.parametrize("tool,kw", [
    ("write_file", {"content": "x"}), ("write_file", {"path": "a.js"}),
    ("read_file", {}), ("edit_file", {"old_text": "a"}),
    ("edit_file", {"path": "game.js", "new_text": "x"})])
def test_a_missing_argument_is_reported_never_guessed(tools, tool, kw):
    r = tools[tool](**kw)
    assert r["ok"] is False and r["error"]


def test_an_omitted_new_text_does_not_silently_delete(tools, tmp_path):
    r = tools["edit_file"](path="game.js", old_text="const b = 2;")
    assert r["ok"] is False and "new_text" in r["error"]
    assert (tmp_path / "game" / "game.js").read_text() == SRC


def test_an_explicit_empty_new_text_still_deletes(tools, tmp_path):
    r = tools["edit_file"](path="game.js", old_text="const b = 2;\n", new_text="")
    assert r["ok"] is True
    assert "const b" not in (tmp_path / "game" / "game.js").read_text()


def test_a_wrong_type_is_reported_not_coerced(tools, tmp_path):
    """Coercing an object body into JSON was a guess at intent. The harness reported the TypeError
    and the model resent a string; that is the whole recovery mechanism."""
    r = tools["write_file"](path="assets.json", content={"images": []})
    assert r["ok"] is False and "TypeError" in r["error"]
    assert not (tmp_path / "game" / "assets.json").exists()


def test_a_path_escape_is_reported_not_raised(tools):
    """The guard still holds; it answers the model instead of killing the completion."""
    r = tools["write_file"](path="../escaped.js", content="x")
    assert r["ok"] is False and "escapes" in r["error"]
