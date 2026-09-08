"""Telling the model what it has already read.

Measured 2026-09-08: 90 of 95 post-compaction reads were of files the model had already read in the
same build. The code map says what the project contains; it does not say that reading a file again
would return exactly what the model was shown.
"""
from pathlib import Path

from maestro.codegen import file_state
from maestro.codegen.tools import build_tools
from maestro.state import RunState


def _game(tmp_path, files):
    d = tmp_path / "game"
    d.mkdir(exist_ok=True)
    for rel, text in files.items():
        p = d / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    return d


def test_a_file_read_and_untouched_is_named_as_unchanged(tmp_path):
    root = _game(tmp_path, {"main.js": "const a = 1;\n"})
    build_tools(RunState(tmp_path), "b1")["read_file"](path="main.js")
    block = file_state.render(tmp_path, root)
    assert "have already read these files and they have NOT changed" in block
    assert "main.js" in block


def test_a_file_written_since_the_read_is_named_as_changed(tmp_path):
    root = _game(tmp_path, {"main.js": "const a = 1;\n"})
    tools = build_tools(RunState(tmp_path), "b1")
    tools["read_file"](path="main.js")
    tools["write_file"](path="main.js", content="const a = 2;\n")
    block = file_state.render(tmp_path, root)
    assert "These have changed since you read them" in block
    assert "NOT changed" not in block


def test_a_partial_read_is_recorded_against_the_whole_file(tmp_path):
    """The window is the model's choice of how much to look at; what a compaction has to say is
    whether the file moved under it."""
    root = _game(tmp_path, {"big.js": "".join(f"line {i}\n" for i in range(200))})
    build_tools(RunState(tmp_path), "b1")["read_file"](path="big.js", offset=1, lines=5)
    assert "NOT changed" in file_state.render(tmp_path, root)


def test_a_file_never_read_is_not_in_the_block(tmp_path):
    root = _game(tmp_path, {"main.js": "const a = 1;\n", "other.js": "const b = 2;\n"})
    build_tools(RunState(tmp_path), "b1")["read_file"](path="main.js")
    assert "other.js" not in file_state.render(tmp_path, root)


def test_a_deleted_file_falls_out_of_the_block(tmp_path):
    root = _game(tmp_path, {"gone.js": "const a = 1;\n"})
    build_tools(RunState(tmp_path), "b1")["read_file"](path="gone.js")
    (root / "gone.js").unlink()
    assert file_state.render(tmp_path, root) == ""


def test_nothing_read_is_no_block_at_all(tmp_path):
    root = _game(tmp_path, {"main.js": "const a = 1;\n"})
    assert file_state.render(tmp_path, root) == ""


def test_a_run_dir_that_cannot_be_written_still_builds(tmp_path, monkeypatch):
    """The block is an optimisation, and a build that cannot record a read must still run."""
    _game(tmp_path, {"main.js": "const a = 1;\n"})
    monkeypatch.setattr(Path, "write_text", _raise_oserror)
    build_tools(RunState(tmp_path), "b1")["read_file"](path="main.js")


def _raise_oserror(*a, **k):
    raise OSError("read-only")
