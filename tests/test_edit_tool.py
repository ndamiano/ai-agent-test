"""edit grounding contract + write off-plan guard (maestro/codegen/tools.py)."""
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


@pytest.fixture
def tools(tmp_path):
    _game(tmp_path, {"main.ts": SRC})
    return build_codegen_tools(RunState(tmp_path))


def test_edit_refuses_before_read(tools):
    r = tools["edit"](file="main.ts", old_string="export const B = 2;", new_string="export const B = 3;")
    assert r["ok"] is False
    assert "didn't read" in r["error"]
    assert r["content"] == SRC  # current body handed back in the SAME result


def test_edit_applies_after_read(tools, tmp_path):
    tools["read_file"](file="main.ts")
    r = tools["edit"](file="main.ts", old_string="export const B = 2;", new_string="export const B = 3;")
    assert r["ok"] is True
    assert r["version"] == 1
    assert "export const B = 3;" in (tmp_path / "game" / "main.ts").read_text()


def test_anchor_miss_returns_content_no_write(tools, tmp_path):
    tools["read_file"](file="main.ts")
    r = tools["edit"](file="main.ts", old_string="does not exist", new_string="x")
    assert r["ok"] is False and "not found" in r["error"]
    assert r["content"] == SRC
    assert (tmp_path / "game" / "main.ts").read_text() == SRC  # untouched


def test_ambiguous_anchor_refuses(tools):
    tools["read_file"](file="main.ts")
    r = tools["edit"](file="main.ts", old_string="export const A", new_string="export const Z")
    assert r["ok"] is False and "matched" in r["error"]


def test_edit_result_grounds_next_edit(tools):
    tools["read_file"](file="main.ts")
    r1 = tools["edit"](file="main.ts", old_string="export const B = 2;", new_string="export const B = 3;")
    assert r1["ok"]
    # No re-read: edit #1's result already carried the new body, so edit #2 is grounded.
    r2 = tools["edit"](file="main.ts", old_string="export const B = 3;", new_string="export const B = 4;")
    assert r2["ok"] is True and r2["version"] == 2


def test_external_write_makes_read_stale(tools):
    tools["read_file"](file="main.ts")
    tools["write"](file="main.ts", code="export const A = 9;\n")  # bumps version, model hasn't re-read anchor
    # write grounds the writer, so an edit right after write is allowed (writer saw its own bytes):
    r = tools["edit"](file="main.ts", old_string="export const A = 9;", new_string="export const A = 8;")
    assert r["ok"] is True


def test_partial_read_returns_slice(tools):
    r = tools["read_file"](file="main.ts", offset=1, limit=1)
    assert r["ok"] and r["partial"] is True
    assert r["content"] == "export const B = 2;\n"
    assert r["offset"] == 1 and r["shown_lines"] == 1 and r["total_lines"] == 3


def test_partial_read_does_not_ground_edit(tools):
    tools["read_file"](file="main.ts", offset=0, limit=1)  # a slice, not the whole file
    r = tools["edit"](file="main.ts", old_string="export const B = 2;", new_string="export const B = 3;")
    assert r["ok"] is False and "didn't read" in r["error"]


def test_full_read_still_grounds_after_partial(tools):
    tools["read_file"](file="main.ts", offset=0, limit=1)
    tools["read_file"](file="main.ts")  # full read grounds
    r = tools["edit"](file="main.ts", old_string="export const B = 2;", new_string="export const B = 3;")
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
    r = tools["edit"](file="world.ts", old_string="export const TAG = 1;", new_string="export const TAG = 2;")
    assert r["ok"] is True  # grounded despite the read being elided


def test_write_rejects_off_plan_filename(tools):
    r = tools["write"](file="_bounce_back().ts", code="export const X = 1;\n")
    assert r["ok"] is False
    assert "off-plan" in r["error"]


def test_write_allows_planned_filename(tools):
    r = tools["write"](file="main.ts", code="export const X = 1;\n")
    assert r["ok"] is True
