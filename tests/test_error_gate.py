import json
from pathlib import Path

import pytest

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from maestro.codegen import error_gate


def _game(tmp_path: Path, index: str, **files: str) -> Path:
    gdir = tmp_path / "game"
    gdir.mkdir()
    (gdir / "index.html").write_text(index)
    for name, body in files.items():
        (gdir / name).write_text(body)
    return gdir


# ---------------------------------------------------------------- addresses

def test_parse_address_names_file_and_line(tmp_path):
    gdir = _game(tmp_path, "<html><body></body></html>",
                 **{"game.js": "const a = [1, 2,\nfunction broken( {\n"})
    lines = error_gate._parse_addresses(gdir)
    assert len(lines) == 1
    assert lines[0].startswith("game.js:")


def test_parse_address_reads_inline_scripts(tmp_path):
    gdir = _game(tmp_path, "<html><script>\nlet x = {,};\n</script></html>")
    lines = error_gate._parse_addresses(gdir)
    assert len(lines) == 1
    assert lines[0].startswith("index.html inline script #1")


def test_valid_module_syntax_is_not_flagged(tmp_path):
    gdir = _game(tmp_path, "<html></html>",
                 **{"main.js": "import * as THREE from './three.module.js';\nexport const ok = 1;\n"})
    assert error_gate._parse_addresses(gdir) == []


def test_vendored_files_are_not_checked(tmp_path):
    gdir = _game(tmp_path, "<html></html>",
                 **{"three.module.js": "this is not javascript at all {{{"})
    assert error_gate._parse_addresses(gdir) == []


def test_redeclaration_scan_lists_every_site(tmp_path):
    gdir = _game(tmp_path, "<html></html>",
                 **{"a.js": "const tabW = 1;\n", "b.js": "let y = 2;\nconst tabW = 3;\n"})
    sites = error_gate._declaration_sites("tabW", gdir)
    assert any(s.startswith("a.js:1") for s in sites)
    assert any(s.startswith("b.js:2") for s in sites)


# ---------------------------------------------------------------- the note

def test_note_carries_message_address_and_parse_sentence(tmp_path):
    gdir = _game(tmp_path, "<html></html>", **{"game.js": "const q = [1,\n"})
    note = error_gate.note_for({"message": "SyntaxError: missing ] after element list",
                                "stack": ""}, gdir)
    assert "missing ] after element list" in note
    assert "game.js:" in note
    assert "every occurrence" in note


def test_runtime_note_uses_stack_and_skips_parse_sentence(tmp_path):
    gdir = _game(tmp_path, "<html></html>")
    note = error_gate.note_for(
        {"message": "TypeError: this._doIdle is not a function",
         "stack": "TypeError: this._doIdle is not a function\n    at gameLoop (game.js:769)"},
        gdir)
    assert "game.js:769" in note
    assert "every occurrence" not in note


# ---------------------------------------------------------------- loop control

class _Kickoffs:
    def __init__(self):
        self.calls = []

    def __call__(self, run_id, *, kind, note):
        self.calls.append({"run_id": run_id, "kind": kind, "note": note})


@pytest.fixture
def wired(tmp_path, monkeypatch):
    run_dir = tmp_path / "run"
    gdir = run_dir / "game"
    gdir.mkdir(parents=True)
    (gdir / "index.html").write_text("<html></html>")

    kickoffs = _Kickoffs()

    class FakeRS:
        def __init__(self, run_id):
            self.run_dir = run_dir

    import maestro.codegen.build_chain as build_chain
    import maestro.state
    monkeypatch.setattr(build_chain, "kickoff", kickoffs)
    monkeypatch.setattr(maestro.state, "RunState", FakeRS)
    return run_dir, gdir, kickoffs


def test_thrown_error_enters_fix_machine_with_one_note(wired, monkeypatch):
    run_dir, gdir, kickoffs = wired
    monkeypatch.setattr(error_gate, "probe", lambda g: [
        {"message": "ReferenceError: Game is not defined", "stack": "at onclick (index.html:1)"},
        {"message": "TypeError: also broken", "stack": ""}])
    error_gate.after_build("r1")
    assert len(kickoffs.calls) == 1
    assert "Game is not defined" in kickoffs.calls[0]["note"]
    assert "also broken" not in kickoffs.calls[0]["note"]
    assert json.loads((run_dir / "error_gate.json").read_text())["rounds"] == 1


def test_clean_probe_kicks_nothing(wired, monkeypatch):
    run_dir, gdir, kickoffs = wired
    monkeypatch.setattr(error_gate, "probe", lambda g: [])
    error_gate.after_build("r1")
    assert kickoffs.calls == []


def test_same_error_twice_running_stops(wired, monkeypatch):
    run_dir, gdir, kickoffs = wired
    err = [{"message": "TypeError: stuck", "stack": ""}]
    monkeypatch.setattr(error_gate, "probe", lambda g: err)
    error_gate.after_build("r1")
    error_gate.after_build("r1")
    assert len(kickoffs.calls) == 1


def test_round_cap_stops(wired, monkeypatch):
    run_dir, gdir, kickoffs = wired
    n = [0]

    def churn(g):
        n[0] += 1
        return [{"message": f"TypeError: bug {n[0]}", "stack": ""}]

    monkeypatch.setattr(error_gate, "probe", churn)
    for _ in range(error_gate.MAX_ROUNDS + 3):
        error_gate.after_build("r1")
    assert len(kickoffs.calls) == error_gate.MAX_ROUNDS


# ---------------------------------------------------------------- the real probe

@pytest.mark.parametrize("body,expect", [
    ("<html><body><script>\nundefinedFunction();\n</script></body></html>", 1),
    ("<html><body><script>\ndocument.title = 'fine';\n</script></body></html>", 0),
])
def test_headless_probe_hears_boot_errors(tmp_path, body, expect):
    pytest.importorskip("playwright.sync_api")
    gdir = _game(tmp_path, body)
    errors = error_gate.probe(gdir)
    assert len(errors) == expect
    if expect:
        assert "undefinedFunction" in errors[0]["message"]
