"""The functions a build's program calls."""
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
    return build_tools(RunState(tmp_path), "b1")


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
    r = tools["edit_file"](path="game.js", old_text="const b = 2;", new_text="const b = 2;")
    assert r["ok"] is False and "changes nothing" in r["error"]
    assert "const b = 2;" in (tmp_path / "game" / "game.js").read_text()


def test_edit_needs_no_prior_read(tools):
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
    t = build_tools(RunState(tmp_path), "b1")
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
    r = build_tools(RunState(tmp_path), "b1")["read_file"](path="big.js")
    assert r["ok"] and "cut here, mid-line" in r["content"]
    assert len(r["content"]) < MAX_READ_CHARS + 300


def _numbered(n):
    return "".join(f"line {i} " + "y" * 90 + "\n" for i in range(1, n + 1))


def test_a_window_ends_on_a_line_boundary(tmp_path):
    body = _numbered(400)
    _game(tmp_path, {"big.js": body})
    r = build_tools(RunState(tmp_path), "b1")["read_file"](path="big.js")
    shown = r["content"].split("\n\n[", 1)[0]
    assert body.startswith(shown)
    assert shown.endswith("\n") and len(shown) <= MAX_READ_CHARS


def test_a_whole_file_comes_back_whole(tmp_path):
    body = _numbered(400)
    _game(tmp_path, {"big.js": body})
    r = build_tools(RunState(tmp_path), "b1")["read_file"](path="big.js")
    assert r["content"] == body and r["lines"] == "1-400/400"
    assert "read the rest" not in r["content"]


def test_offset_and_lines_read_one_range_of_a_file(tmp_path):
    body = _numbered(400)
    _game(tmp_path, {"big.js": body})
    read = build_tools(RunState(tmp_path), "b1")["read_file"]
    window = read(path="big.js", offset=100, lines=20)
    assert window["ok"] and window["lines"] == "100-119/400"
    assert window["content"].startswith("line 100 ")
    tail = read(path="big.js", offset=381)
    assert tail["lines"] == "381-400/400" and body.endswith(tail["content"])


def test_offset_past_the_end_is_reported(tmp_path):
    _game(tmp_path, {"small.js": "a\nb\n"})
    r = build_tools(RunState(tmp_path), "b1")["read_file"](path="small.js", offset=9)
    assert r["ok"] is False and "past the end" in r["error"]


def test_a_whole_file_still_reads_from_line_one(tmp_path, tools):
    r = tools["read_file"](path="game.js")
    assert r["content"] == SRC and r["lines"] == "1-3/3"


def test_a_long_single_line_is_not_elided_below_the_ceiling(tmp_path):
    line = "const DATA = [" + ",".join(str(i) for i in range(2000)) + "];\n"
    _game(tmp_path, {"data.js": line})
    r = build_tools(RunState(tmp_path), "b1")["read_file"](path="data.js")
    assert r["content"] == line


def test_read_missing_file_is_an_error(tools):
    assert tools["read_file"](path="nope.js")["ok"] is False


def test_read_requested_but_unrendered_asset_answers_pending_not_missing(tools, tmp_path):
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
    r = tools["write_file"](path="assets.json", content={"images": []})
    assert r["ok"] is False and "content" in r["error"] and "string" in r["error"]
    assert not (tmp_path / "game" / "assets.json").exists()


def test_a_path_escape_is_reported_not_raised(tools):
    r = tools["write_file"](path="../escaped.js", content="x")
    assert r["ok"] is False and "escapes" in r["error"]


def test_lines_reads_one_function_by_its_range(tmp_path):
    body = _numbered(400)
    _game(tmp_path, {"big.js": body})
    r = build_tools(RunState(tmp_path), "b1")["read_file"](path="big.js", offset=53, lines=25)
    assert r["ok"] and r["lines"] == "53-77/400"
    assert r["content"] == "".join(body.splitlines(keepends=True)[52:77])
    assert "too long to read" not in r["content"]


def test_check_syntax_finds_the_file_that_does_not_parse(tmp_path):
    _game(tmp_path, {"good.js": "export const a = 1;\n",
                     "bad.js": "export const = ;\n",
                     "js/deep.js": "const y = {;\n"})
    checked = build_tools(RunState(tmp_path), "b1")["check_syntax"]()["checked"]
    assert checked["good.js"] == "OK"
    assert checked["bad.js"].startswith("line 1: Unexpected token")
    assert checked["js/deep.js"].startswith("line 1")


def test_check_syntax_reads_a_module_as_a_module(tmp_path):
    _game(tmp_path, {"m.js": "export const a = ;\n"})
    checked = build_tools(RunState(tmp_path), "b1")["check_syntax"]()["checked"]
    assert checked["m.js"] != "OK"


def test_check_syntax_leaves_the_vendored_renderer_alone(tmp_path):
    from maestro.codegen.staging import seed_vendor
    seed_vendor(tmp_path)
    (tmp_path / "game" / "mine.js").write_text("const a = 1;\n")
    checked = build_tools(RunState(tmp_path), "b1")["check_syntax"]()["checked"]
    assert set(checked) == {"mine.js"}


def test_check_syntax_names_the_file_a_game_loads_but_does_not_have(tmp_path):
    _game(tmp_path, {"js/main.js": "import { sfx } from './lib/audio.js';\nsfx('hit');\n",
                     "index.html": '<script type="module" src="js/main.js"></script>'})
    (tmp_path / "game" / "lib").mkdir()
    (tmp_path / "game" / "lib" / "audio.js").write_text("export const sfx = () => {};\n")
    checked = build_tools(RunState(tmp_path), "b1")["check_syntax"]()["checked"]
    assert "loads a file that is not there: ./lib/audio.js" in checked["js/main.js"]
    assert checked["index.html"] == "OK"


def test_check_syntax_leaves_a_reference_that_resolves_alone(tmp_path):
    _game(tmp_path, {"js/main.js": "import { sfx } from '../lib/audio.js';\n",
                     "index.html": '<script type="module" src="js/main.js"></script>'})
    (tmp_path / "game" / "lib").mkdir()
    (tmp_path / "game" / "lib" / "audio.js").write_text("export const sfx = () => {};\n")
    checked = build_tools(RunState(tmp_path), "b1")["check_syntax"]()["checked"]
    assert checked == {"js/main.js": "OK", "index.html": "OK"}


def test_check_syntax_does_not_call_queued_art_missing(tmp_path):
    import json

    from maestro.codegen.assets import manifest_path
    _game(tmp_path, {"game.js": "const img = 'assets/hero.webp';\nnew Image().src = img;\n",
                     "index.html": '<img src="assets/hero.webp"><img src="assets/gone.webp">'})
    manifest_path(tmp_path).write_text(json.dumps(
        {"images": [{"id": "hero", "prompt": "a hero", "file": "assets/hero.webp"}]}))
    checked = build_tools(RunState(tmp_path), "b1")["check_syntax"]()["checked"]
    assert checked["index.html"] == "loads a file that is not there: assets/gone.webp"


def test_check_syntax_names_a_file_that_is_not_there(tmp_path):
    _game(tmp_path, {})
    checked = build_tools(RunState(tmp_path), "b1")["check_syntax"](paths=["gone.js"])["checked"]
    assert checked["gone.js"] == "no such file: gone.js"


def test_done_carries_its_summary_back(tmp_path):
    _game(tmp_path, {})
    assert build_tools(RunState(tmp_path), "b1")["done"](summary="it plays") == {
        "ok": True, "summary": "it plays"}


_GAME_HTML = """<!doctype html><html><body><script>
window.__game = { x: 0, keys: [],
  start() { return {phase: 'playing'}; },
  getState() { return {x: this.x, keys: this.keys}; } };
addEventListener('keydown', e => { window.__game.keys.push(e.code); window.__game.x += 1; });
console.log('booted');
</script></body></html>"""


@pytest.fixture
def playable(tmp_path):
    pytest.importorskip("playwright.sync_api")
    _game(tmp_path, {"index.html": _GAME_HTML})
    return build_tools(RunState(tmp_path), "b1")["play"]


@pytest.mark.browser
def test_play_returns_what_the_script_returns(playable):
    r = playable(js="console.log('in play'); __game.start(); return __game.getState();")
    assert r["ok"] and r["result"] == {"x": 0, "keys": []}
    assert r["errors"] == [] and r["console"] == ["in play"]


@pytest.mark.browser
def test_play_presses_a_real_key_through_the_pages_own_handler(playable):
    r = playable(js="await __press('KeyD', 50); return __game.getState();")
    assert r["result"]["keys"] == ["KeyD"] and r["result"]["x"] == 1


@pytest.mark.browser
def test_play_reports_what_the_script_threw_and_what_the_page_threw(playable):
    r = playable(js="setTimeout(() => { undefinedThing(); }, 0); await new Promise(r => setTimeout(r, 100)); throw new Error('nope');")
    assert r["ok"] is False and "nope" in r["error"]
    assert any("undefinedThing" in e["message"] for e in r["errors"])


@pytest.mark.browser
def test_play_times_out_a_script_that_never_yields_instead_of_hanging(playable):
    r = playable(js="while (true) {}", seconds=1)
    assert r["ok"] is False and ("still running" in r["error"] or "killed" in r["error"])


@pytest.mark.browser
def test_play_refuses_a_page_that_never_installs_the_debug_api(tmp_path):
    pytest.importorskip("playwright.sync_api")
    _game(tmp_path, {"index.html": "<html><body>no api</body></html>"})
    r = build_tools(RunState(tmp_path), "b1")["play"](js="return 1;")
    assert r["ok"] is False and "__game never appeared" in r["error"]


@pytest.mark.browser
def test_play_blocks_and_reports_a_request_that_tries_to_leave(playable):
    r = playable(js="try { await fetch('https://example.com/'); } catch (e) {} return 1;")
    assert r["ok"] and r["result"] == 1
    assert any("example.com" in b for b in r.get("blocked", []))


DESIGN = """# 11. DEFINITION OF DONE

## Tier 1 Definition of Done

- [ ] Syntax check passes.
- [ ] Game boots to `title` screen.
- [ ] Pylon can be placed, upgraded, and sold.

## Tier 2 Definition of Done

- [ ] Syntax check passes.
- [ ] All 6 maps load and validate.
"""


def test_check_off_ticks_a_list_of_rows_and_reports_what_is_left(tools, tmp_path):
    _game(tmp_path, {"design/design.md": DESIGN})
    res = tools["check_off"](rows=["- [ ] Syntax check passes.", "game boots to `TITLE` screen",
                                   "placed, upgraded", "Music plays."])
    assert res["ok"] is True
    assert res["ticked"] == ["Syntax check passes.", "Game boots to `title` screen.",
                             "Pylon can be placed, upgraded, and sold."]
    assert res["unmatched"] == ["Music plays."]
    assert res["unchecked"] == ["Syntax check passes.", "All 6 maps load and validate."]
    body = (tmp_path / "game" / "design" / "design.md").read_text()
    assert body.count("- [x] ") == 3 and body.count("- [ ] ") == 2
    assert body.index("- [x] Syntax") < body.index("- [ ] Syntax")
    again = tools["check_off"](rows="Syntax check passes.")
    assert again["ticked"] == ["Syntax check passes."] and again["unchecked"] == ["All 6 maps load and validate."]


def test_check_off_without_a_design_says_so(tools):
    assert tools["check_off"](rows=["anything"])["ok"] is False
