"""edit grounding + multi-hunk atomicity, and write's create-only contract (maestro/codegen/tools.py)."""
import json

import pytest

from maestro.codegen.tools import build_codegen_tools
from maestro.state import RunState

SRC = 'export const A = 1;\nexport const B = 2;\nexport const A2 = 1;\n'


def _game(tmp_path, files):
    d = tmp_path / "game"
    d.mkdir(exist_ok=True)
    names = []
    for name, body in files.items():
        (d / name).write_text(body, encoding="utf-8")
        names.append({"name": name, "purpose": "", "exports": []})
    (d / "manifest.json").write_text(json.dumps({"files": names}), encoding="utf-8")


def _hunk(old, new):
    return {"old_string": old, "new_string": new}


@pytest.fixture
def tools(tmp_path):
    _game(tmp_path, {"main.ts": SRC})
    return build_codegen_tools(RunState(tmp_path))


# ── edit grounding ────────────────────────────────────────────────────────────
def test_edit_refuses_before_read(tools):
    r = tools["edit"](file="main.ts", edits=[_hunk("export const B = 2;", "export const B = 3;")])
    assert r["ok"] is False
    assert "didn't read" in r["error"]
    assert r["content"] == SRC  # current body handed back in the SAME result


def test_edit_applies_after_read(tools, tmp_path):
    tools["read_file"](file="main.ts")
    r = tools["edit"](file="main.ts", edits=[_hunk("export const B = 2;", "export const B = 3;")])
    assert r["ok"] is True
    assert r["version"] == 1
    assert "export const B = 3;" in (tmp_path / "game" / "main.ts").read_text()


def test_anchor_miss_returns_content_no_write(tools, tmp_path):
    tools["read_file"](file="main.ts")
    r = tools["edit"](file="main.ts", edits=[_hunk("does not exist", "x")])
    assert r["ok"] is False and "not found" in r["error"]
    assert r["content"] == SRC
    assert (tmp_path / "game" / "main.ts").read_text() == SRC  # untouched


def test_ambiguous_anchor_refuses(tools):
    tools["read_file"](file="main.ts")
    r = tools["edit"](file="main.ts", edits=[_hunk("export const A", "export const Z")])
    assert r["ok"] is False and "matched" in r["error"]


def test_empty_edits_refused_with_content(tools):
    tools["read_file"](file="main.ts")
    r = tools["edit"](file="main.ts", edits=[])
    assert r["ok"] is False and "no edits" in r["error"]
    assert r["content"] == SRC


def test_edit_result_grounds_next_edit(tools):
    tools["read_file"](file="main.ts")
    r1 = tools["edit"](file="main.ts", edits=[_hunk("export const B = 2;", "export const B = 3;")])
    assert r1["ok"]
    # No re-read: edit #1's result already carried the new body, so edit #2 is grounded.
    r2 = tools["edit"](file="main.ts", edits=[_hunk("export const B = 3;", "export const B = 4;")])
    assert r2["ok"] is True and r2["version"] == 2


# ── multi-hunk atomicity ──────────────────────────────────────────────────────
def test_multi_hunk_applies_all_with_single_version_bump(tools, tmp_path):
    tools["read_file"](file="main.ts")
    r = tools["edit"](file="main.ts", edits=[
        _hunk("export const A = 1;", "export const A = 10;"),
        _hunk("export const B = 2;", "export const B = 20;"),
        _hunk("export const A2 = 1;", "export const A2 = 30;"),
    ])
    assert r["ok"] is True and r["applied"] == 3
    assert r["version"] == 1   # ONE bump for the whole batch
    body = (tmp_path / "game" / "main.ts").read_text()
    assert body == "export const A = 10;\nexport const B = 20;\nexport const A2 = 30;\n"
    assert r["content"] == body


def test_multi_hunk_second_miss_applies_nothing(tools, tmp_path):
    tools["read_file"](file="main.ts")
    r = tools["edit"](file="main.ts", edits=[
        _hunk("export const A = 1;", "export const A = 10;"),
        _hunk("no such text", "y"),
        _hunk("export const A2 = 1;", "export const A2 = 30;"),
    ])
    assert r["ok"] is False
    assert "hunk 2/3" in r["error"] and "not found" in r["error"]
    assert (tmp_path / "game" / "main.ts").read_text() == SRC   # nothing applied
    assert r["content"] == SRC


def test_overlapping_hunks_rejected(tools, tmp_path):
    tools["read_file"](file="main.ts")
    r = tools["edit"](file="main.ts", edits=[
        _hunk("export const B = 2;\nexport const A2 = 1;", "z"),
        _hunk("const B = 2;", "const B = 9;"),   # inside hunk 1's span
    ])
    assert r["ok"] is False and "overlaps" in r["error"]
    assert (tmp_path / "game" / "main.ts").read_text() == SRC


def test_multi_hunk_empty_old_string_appends(tools):
    # empty old_string = APPEND (grounded, atomic with the batch) — a tail-anchor append is the
    # flimsiest edit there is; a full live fix budget failed to land a one-line append that way.
    tools["read_file"](file="main.ts")
    r = tools["edit"](file="main.ts", edits=[
        _hunk("export const A = 1;", "export const A = 2;"), _hunk("", "const tail = true;")])
    assert r["ok"] and r["content"].rstrip().endswith("const tail = true;")
    assert "export const A = 2;" in r["content"]


def test_append_hunk_requires_content(tools):
    tools["read_file"](file="main.ts")
    r = tools["edit"](file="main.ts", edits=[_hunk("", "   ")])
    assert r["ok"] is False and "append" in r["error"]


def test_uniqueness_judged_against_original_content(tools, tmp_path):
    # Hunk 1's replacement introduces text matching hunk 2's anchor — uniqueness/spans are judged on
    # the ORIGINAL body, so hunk 2 still lands on the original occurrence, not inside hunk 1's output.
    tools["read_file"](file="main.ts")
    r = tools["edit"](file="main.ts", edits=[
        _hunk("export const A = 1;", "export const B = 2; // shadow"),
        _hunk("export const B = 2;\n", "export const B = 5;\n"),
    ])
    assert r["ok"] is True
    body = (tmp_path / "game" / "main.ts").read_text()
    assert body == "export const B = 2; // shadow\nexport const B = 5;\nexport const A2 = 1;\n"


# ── GENERATED files are pipeline-owned ────────────────────────────────────────
def test_edit_refuses_generated_scaffold_and_points_to_hooks(tmp_path):
    gen = ("// GENERATED control scaffold — never edit; gameplay lives in game.ts and its siblings.\n"
           "export const X = 1;\n")
    _game(tmp_path, {"main.ts": gen, "game.ts": "export const Y = 2;\n"})
    tools = build_codegen_tools(RunState(tmp_path))
    tools["read_file"](file="main.ts")
    r = tools["edit"](file="main.ts", edits=[_hunk("export const X = 1;", "export const X = 2;")])
    assert r["ok"] is False
    assert "GENERATED" in r["error"] and "game.ts" in r["error"]   # names the hook file to change
    assert (tmp_path / "game" / "main.ts").read_text() == gen      # untouched


def test_edit_refuses_generated_data_ts_and_points_to_json(tmp_path):
    gen = ("// GENERATED from data/*.json — never edit by hand; change the JSON instead.\n"
           "export const ENEMIES = [];\n")
    _game(tmp_path, {"main.ts": SRC, "data.ts": gen})
    tools = build_codegen_tools(RunState(tmp_path))
    r = tools["edit"](file="data.ts", edits=[_hunk("export const ENEMIES = [];", "x")])
    assert r["ok"] is False and "data/*.json" in r["error"]


# ── write: create-only ────────────────────────────────────────────────────────
def test_write_refuses_existing_nonempty_file(tools, tmp_path):
    r = tools["write"](file="main.ts", code="export const X = 1;\n")
    assert r["ok"] is False
    assert "already exists" in r["error"] and "edit" in r["error"]
    assert r["content"] == SRC   # current body handed back so the turn isn't wasted
    assert (tmp_path / "game" / "main.ts").read_text() == SRC


def test_write_refusal_grounds_edit(tools):
    # The refusal carries the body (same re-anchor convention as an edit miss) — the very next edit
    # is grounded without a read_file round-trip.
    r = tools["write"](file="main.ts", code="export const X = 1;\n")
    assert r["ok"] is False
    r2 = tools["edit"](file="main.ts", edits=[_hunk("export const B = 2;", "export const B = 3;")])
    assert r2["ok"] is True


def test_write_creates_absent_planned_file(tmp_path):
    d = tmp_path / "game"
    d.mkdir()
    (d / "main.ts").write_text(SRC, encoding="utf-8")
    (d / "manifest.json").write_text(json.dumps({"files": [
        {"name": "main.ts"}, {"name": "extra.ts"}]}), encoding="utf-8")
    tools = build_codegen_tools(RunState(tmp_path))
    r = tools["write"](file="extra.ts", code="export const Y = 2;\n")
    assert r["ok"] is True
    assert (d / "extra.ts").read_text() == "export const Y = 2;\n"


def test_write_lands_on_empty_file(tmp_path):
    # a file that exists but is whitespace-only has no code to destabilize — creation, not overwrite.
    _game(tmp_path, {"main.ts": SRC, "empty.ts": "  \n"})
    tools = build_codegen_tools(RunState(tmp_path))
    r = tools["write"](file="empty.ts", code="export const Z = 3;\n")
    assert r["ok"] is True
    assert (tmp_path / "game" / "empty.ts").read_text() == "export const Z = 3;\n"


def test_write_rejects_empty_code(tools):
    assert tools["write"](file="main.ts", code="  ")["ok"] is False


def test_write_rejects_off_plan_filename(tools):
    r = tools["write"](file="_bounce_back().ts", code="export const X = 1;\n")
    assert r["ok"] is False
    assert "off-plan" in r["error"]


# ── reads: partial / elision / cap ────────────────────────────────────────────
def test_partial_read_returns_slice(tools):
    r = tools["read_file"](file="main.ts", offset=1, limit=1)
    assert r["ok"] and r["partial"] is True
    assert r["content"] == "export const B = 2;\n"
    assert r["offset"] == 1 and r["shown_lines"] == 1 and r["total_lines"] == 3


def test_partial_read_does_not_ground_edit(tools):
    tools["read_file"](file="main.ts", offset=0, limit=1)  # a slice, not the whole file
    r = tools["edit"](file="main.ts", edits=[_hunk("export const B = 2;", "export const B = 3;")])
    assert r["ok"] is False and "didn't read" in r["error"]


def test_full_read_still_grounds_after_partial(tools):
    tools["read_file"](file="main.ts", offset=0, limit=1)
    tools["read_file"](file="main.ts")  # full read grounds
    r = tools["edit"](file="main.ts", edits=[_hunk("export const B = 2;", "export const B = 3;")])
    assert r["ok"] is True


def test_read_elides_giant_inline_line(tmp_path):
    # worldgen bakes a 100KB heightfield onto ONE line; the model needs the sibling function, not data.
    data = '{"height":[' + ",".join("0.5" for _ in range(40_000)) + "]}"
    world = f"export const WORLD: any = {data};\nexport function heightAt(x, z) {{ return 0; }}\n"
    _game(tmp_path, {"main.ts": SRC, "world.ts": world})
    tools = build_codegen_tools(RunState(tmp_path))
    r = tools["read_file"](file="world.ts")
    assert r["ok"] and r["elided"] is True
    assert "chars of inline data elided" in r["content"]
    assert "export function heightAt" in r["content"]  # the code survives
    assert len(r["content"]) < 2_000            # 160KB line collapsed to head + marker


def test_read_caps_over_long_result(tmp_path):
    from maestro.codegen.tools import MAX_READ_CHARS
    body = "\n".join(f"const v{i} = {i};" for i in range(4_000))  # many short lines, no giant line
    _game(tmp_path, {"main.ts": SRC, "big.ts": body})
    tools = build_codegen_tools(RunState(tmp_path))
    r = tools["read_file"](file="big.ts")
    assert r["ok"] and r["truncated"] is True and r["elided"] is False
    assert "chars truncated" in r["content"]
    assert len(r["content"]) <= MAX_READ_CHARS + 200
    assert r["content"].startswith("const v0 = 0;")   # head kept
    assert r["content"].rstrip().endswith(";")         # tail kept


def test_read_short_file_unchanged(tools):
    r = tools["read_file"](file="main.ts")
    assert r["ok"] and r["elided"] is False and r["truncated"] is False
    assert r["content"] == SRC


def test_edit_grounds_after_elided_read(tmp_path):
    # elision changes the returned bytes but not grounding: an anchor on real code still lands.
    data = '[' + ",".join("1" for _ in range(40_000)) + "]"
    world = f"export const WORLD: any = {data};\nexport const TAG = 1;\n"
    _game(tmp_path, {"main.ts": SRC, "world.ts": world})
    tools = build_codegen_tools(RunState(tmp_path))
    tools["read_file"](file="world.ts")
    r = tools["edit"](file="world.ts", edits=[_hunk("export const TAG = 1;", "export const TAG = 2;")])
    assert r["ok"] is True  # grounded despite the read being elided
