"""The five tools (maestro/codegen/tools.py) — deliberately the smallest surface that works."""
import json

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


@pytest.mark.parametrize("path", ["../escaped.js", "../../etc/passwd", "a/../../out.js"])
def test_a_path_cannot_escape_the_game_folder(tools, tmp_path, path):
    assert tools["write_file"](path=path, content="x")["ok"] is False
    assert not (tmp_path.parent / "escaped.js").exists()


def test_nested_paths_are_allowed(tools, tmp_path):
    assert tools["write_file"](path="assets/cards.json", content="[]")["ok"] is True
    assert (tmp_path / "game" / "assets" / "cards.json").read_text() == "[]"


def test_write_overwrites(tools, tmp_path):
    tools["write_file"](path="game.js", content="const x = 1;\n")
    assert (tmp_path / "game" / "game.js").read_text() == "const x = 1;\n"


def test_edit_replaces_a_unique_snippet(tools, tmp_path):
    r = tools["edit_file"](path="game.js", old_text="const b = 2;", new_text="const b = 3;")
    assert r["ok"] is True
    assert "const b = 3;" in (tmp_path / "game" / "game.js").read_text()


def test_edit_that_changes_nothing_is_refused(tools, tmp_path):
    """A 27B stuck at the end of a long build repeats one edit whose old and new text are the
    same, and a tool that answers ok to it keeps the loop fed — measured at 10 and 25 identical
    trailing calls in two capped builds (2026-08-24)."""
    r = tools["edit_file"](path="game.js", old_text="const b = 2;", new_text="const b = 2;")
    assert r["ok"] is False and "changes nothing" in r["error"]
    assert "const b = 2;" in (tmp_path / "game" / "game.js").read_text()


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


def test_edit_names_double_escaping_as_the_reason(tools, tmp_path):
    r = tools["edit_file"](path="game.js", old_text="const a = 1;\\nconst b = 2;", new_text="x")
    assert r["ok"] is False and "escaped twice" in r["error"]
    assert (tmp_path / "game" / "game.js").read_text() == SRC


def test_edit_names_double_escaped_quotes_too(tmp_path):
    _game(tmp_path, {"index.html": '<div id="x">\n  <p>hi</p>\n</div>\n'})
    t = build_tools(RunState(tmp_path))
    r = t["edit_file"](path="index.html", old_text='<div id=\\"x\\">\\n  <p>hi</p>', new_text="x")
    assert r["ok"] is False and "escaped twice" in r["error"]


def test_edit_does_not_unmangle_the_text_it_reports(tools, tmp_path):
    r = tools["edit_file"](path="game.js", old_text="const a = 1;\\nconst b = 2;", new_text="x")
    assert r["ok"] is False
    assert (tmp_path / "game" / "game.js").read_text() == SRC


def test_write_refuses_a_file_that_is_one_escaped_line(tools, tmp_path):
    r = tools["write_file"](path="new.js", content="const a = 1;\\nconst b = 2;")
    assert r["ok"] is False and "escaped twice" in r["error"]
    assert not (tmp_path / "game" / "new.js").exists()


def test_write_allows_a_real_newline_beside_an_escaped_one(tools, tmp_path):
    body = 'const s = "a\\nb";\nconst t = 2;\n'
    assert tools["write_file"](path="new.js", content=body)["ok"] is True
    assert (tmp_path / "game" / "new.js").read_text() == body


def test_edit_on_a_missing_file_is_an_error(tools):
    assert tools["edit_file"](path="nope.js", old_text="a", new_text="b")["ok"] is False


def test_read_returns_the_whole_file(tools):
    assert tools["read_file"](path="game.js")["content"] == SRC


def test_a_single_line_past_the_ceiling_says_it_was_cut_mid_line(tmp_path):
    _game(tmp_path, {"big.js": "x" * (MAX_READ_CHARS + 500)})
    r = build_tools(RunState(tmp_path))["read_file"](path="big.js")
    assert r["ok"] and "cut here, mid-line" in r["content"]
    assert len(r["content"]) < MAX_READ_CHARS + 300


def _numbered(n):
    return "".join(f"line {i} " + "y" * 90 + "\n" for i in range(1, n + 1))


def test_a_window_ends_on_a_line_boundary(tmp_path):
    """The measured loop: a read cut mid-line is copied into old_text, where it matches nothing."""
    body = _numbered(400)
    _game(tmp_path, {"big.js": body})
    r = build_tools(RunState(tmp_path))["read_file"](path="big.js")
    shown = r["content"].split("\n\n[", 1)[0]
    assert body.startswith(shown)
    assert shown.endswith("\n") and len(shown) <= MAX_READ_CHARS


def test_the_note_says_where_to_read_the_rest_and_to_split_the_file(tmp_path):
    _game(tmp_path, {"big.js": _numbered(400)})
    r = build_tools(RunState(tmp_path))["read_file"](path="big.js")
    assert "of 400" in r["content"] and "worth splitting" in r["content"]
    assert f"offset {int(r['lines'].split('-')[1].split('/')[0]) + 1}" in r["content"]


def test_offset_reaches_the_tail_a_first_read_could_not_show(tmp_path):
    body = _numbered(400)
    _game(tmp_path, {"big.js": body})
    read = build_tools(RunState(tmp_path))["read_file"]
    first = read(path="big.js")
    nxt = int(first["lines"].split("-")[1].split("/")[0]) + 1
    rest = read(path="big.js", offset=nxt)
    assert rest["ok"] and rest["lines"] == f"{nxt}-400/400"
    assert body.endswith(rest["content"])


def test_offset_past_the_end_is_reported(tmp_path):
    _game(tmp_path, {"small.js": "a\nb\n"})
    r = build_tools(RunState(tmp_path))["read_file"](path="small.js", offset=9)
    assert r["ok"] is False and "past the end" in r["error"]


def test_a_whole_file_still_reads_from_line_one(tmp_path, tools):
    r = tools["read_file"](path="game.js")
    assert r["content"] == SRC and r["lines"] == "1-3/3"


def test_a_long_single_line_is_not_elided_below_the_ceiling(tmp_path):
    """Mid-file elision broke edit anchors; the harness only ever truncated the tail."""
    line = "const DATA = [" + ",".join(str(i) for i in range(2000)) + "];\n"
    _game(tmp_path, {"data.js": line})
    r = build_tools(RunState(tmp_path))["read_file"](path="data.js")
    assert r["content"] == line


def test_read_missing_file_is_an_error(tools):
    assert tools["read_file"](path="nope.js")["ok"] is False


def test_read_requested_but_unrendered_asset_answers_pending_not_missing(tools, tmp_path):
    # "no such file" on a queued render reads as a failed ask and the model re-requests its art
    # under new ids. ok=True keeps the repeat ledger quiet on a legitimate second look.
    _game(tmp_path, {"assets.json": json.dumps({"images": [
        {"id": "goblin", "file": "assets/goblin.webp", "kind": "sprite", "prompt": "a goblin"}]})})
    r = tools["read_file"](path="assets/goblin.webp")
    assert r["ok"] is True
    assert r["pending"] is True
    assert "do not request it again" in r["note"]
    assert "goblin" in r["note"]


def test_read_missing_asset_nobody_requested_is_still_an_error(tools, tmp_path):
    _game(tmp_path, {"assets.json": json.dumps({"images": [
        {"id": "goblin", "file": "assets/goblin.webp", "kind": "sprite", "prompt": "a goblin"}]})})
    assert tools["read_file"](path="assets/orc.webp")["ok"] is False


def test_read_vendor_renderer_names_its_purpose_instead_of_its_4k_lines(tools, tmp_path):
    # Only the seeded copy at the game root is vendor; the model's own file under a subdir with
    # a colliding name stays readable.
    (tmp_path / "game" / "GLTFLoader.js").write_text("// thousands of lines of loader\n")
    r = tools["read_file"](path="GLTFLoader.js")
    assert r["ok"] is False
    assert "vendored renderer" in r["error"] and "import" in r["error"].lower()
    (tmp_path / "game" / "lib").mkdir()
    (tmp_path / "game" / "lib" / "GLTFLoader.js").write_text("// mine\n")
    assert tools["read_file"](path="lib/GLTFLoader.js")["ok"] is True


def test_read_binary_names_itself_instead_of_leaking_bytes(tools, tmp_path):
    (tmp_path / "game" / "assets").mkdir()
    (tmp_path / "game" / "assets" / "hut.glb").write_bytes(b"glTF\x02\x00\x00\x00" + b"\x00" * 64)
    r = tools["read_file"](path="assets/hut.glb")
    assert r["ok"] is False
    assert "binary" in r["error"] and "hut.glb" in r["error"]
    assert "�" not in r["error"]


def test_list_files_skips_scratch(tools, tmp_path):
    (tmp_path / "game" / "_transcript.jsonl").write_text("x")
    assert [f["path"] for f in tools["list_files"]()["files"]] == ["game.js"]


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
