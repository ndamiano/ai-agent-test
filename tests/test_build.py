"""The build path: file-path safety, the turn machine, transcript compaction, staging."""
import json

import pytest

from maestro.codegen import build_steps
from maestro.codegen.build_state import BuildCursor
from maestro.codegen.staging import stage_for_play
from maestro.codegen.tools import build_tools
from maestro.state import RunState


@pytest.fixture
def tools(tmp_path):
    (tmp_path / "game").mkdir(exist_ok=True)
    return build_tools(RunState(tmp_path))


def _cursor(**kw):
    return BuildCursor(build_id="b1", **kw)


def _reply(content="", calls=None, usage=None):
    msg = {"role": "assistant", "content": content}
    if calls:
        msg["tool_calls"] = [
            {"id": f"c{i}", "type": "function",
             "function": {"name": n, "arguments": json.dumps(a)}}
            for i, (n, a) in enumerate(calls)]
    return {"choices": [{"message": msg}], "usage": usage or {}}


# ── compaction ────────────────────────────────────────────────────────────────
def _history():
    h = [{"role": "user", "content": "make a game"}]
    for i in range(6):
        h.append({"role": "assistant", "content": f"step {i}",
                  "tool_calls": [{"id": f"c{i}", "type": "function",
                                  "function": {"name": "write", "arguments": "{}"}}]})
        h.append({"role": "tool", "tool_call_id": f"c{i}", "content": "x" * 400})
    return h


def test_rounds_never_split_a_tool_result():
    groups = build_steps.rounds(_history())
    assert all(g[0]["role"] != "tool" for g in groups)
    assert sum(len(g) for g in groups) == len(_history()) - 1


def test_compact_drops_oldest_and_regrounds(tmp_path):
    (tmp_path / "game").mkdir()
    (tmp_path / "game" / "game.js").write_text("x")
    cursor = _cursor(history=_history())
    assert build_steps.compact(tmp_path, cursor, keep_chars=1200) > 0
    assert cursor.history[0]["content"] == "make a game"     # the request is never dropped
    assert "game.js" in cursor.history[1]["content"]         # re-grounded on the real file list
    # No orphan: every tool result still follows an assistant message carrying its call id.
    open_ids = set()
    for m in cursor.history:
        if m.get("tool_calls"):
            open_ids |= {c["id"] for c in m["tool_calls"]}
        if m["role"] == "tool":
            assert m["tool_call_id"] in open_ids


def test_compact_noop_when_it_fits(tmp_path):
    (tmp_path / "game").mkdir()
    cursor = _cursor(history=_history())
    before = list(cursor.history)
    assert build_steps.compact(tmp_path, cursor, keep_chars=10_000_000) == 0
    assert cursor.history == before


# ── the turn machine ──────────────────────────────────────────────────────────
def test_the_first_turn_sends_the_request_and_nothing_else(tmp_path, tools):
    """The brief is for the human and the audit. Prepending its restated mechanics puts a second,
    more concrete instruction beside the request on every turn — the small-model failure mode."""
    cursor = _cursor()
    spec = {"request": "a card game", "design": {"look": "inky woodcut", "audio": "lute",
                                                 "mechanics": ["draw five cards a turn"]}}
    out = build_steps.step(spec, tmp_path, tools, cursor, {})
    assert isinstance(out, build_steps.Infer)
    assert out.messages[-1]["content"] == "a card game"
    assert {t["function"]["name"] for t in out.schemas} == {
        "list_files", "read_file", "write_file", "edit_file", "generate_media", "done"}


def test_the_system_prompt_stays_the_measured_one(tmp_path, tools):
    """Every line here is read on every turn of every build. Additions regressed the artifact once
    already, so the prompt is pinned: the grid's six rules, plus the renderer and the media tool —
    the two things in the project the model cannot infer from a file listing."""
    cursor = _cursor()
    out = build_steps.step({"request": "a card game"}, tmp_path, tools, cursor, {})
    system = out.messages[0]["content"]
    assert system.count("\n- ") == 8            # exactly the eight rules
    assert "three.module.js" in system and "generate_media" in system
    # assets.json is written by the platform, so naming it here would invite the model to write it.
    for absent in ("assets.json", "WASD", "window"):
        assert absent not in system


def test_fix_note_replaces_the_request(tmp_path, tools):
    cursor = _cursor(request="the player cannot move")
    out = build_steps.step({"request": "a card game"}, tmp_path, tools, cursor, {})
    assert "the player cannot move" in out.messages[-1]["content"]


def test_done_ends_the_build(tmp_path, tools):
    cursor = _cursor()
    build_steps.step({}, tmp_path, tools, cursor, {})
    out = build_steps.step({}, tmp_path, tools, cursor,
                           _reply(calls=[("done", {"summary": "shipped"})]))
    assert isinstance(out, build_steps.Done)
    assert cursor.finished is True and "shipped" in out.report


def test_tool_call_lands_on_disk_and_continues(tmp_path, tools):
    cursor = _cursor()
    build_steps.step({}, tmp_path, tools, cursor, {})
    out = build_steps.step({}, tmp_path, tools, cursor,
                           _reply(calls=[("write_file", {"path": "index.html", "content": "<h1>hi</h1>"})]))
    assert isinstance(out, build_steps.Infer)
    assert (tmp_path / "game" / "index.html").read_text() == "<h1>hi</h1>"
    assert cursor.history[-1]["role"] == "tool"


def test_the_step_report_says_what_the_turn_did(tmp_path, tools):
    """The feed line is the only view a watcher has of a running build, so it names the files the
    turn touched rather than counting turns."""
    cursor = _cursor()
    build_steps.step({}, tmp_path, tools, cursor, {})
    out = build_steps.step({}, tmp_path, tools, cursor, _reply(calls=[
        ("write_file", {"path": "index.html", "content": "<h1>hi</h1>"}),
        ("read_file", {"path": "index.html"}),
    ]))
    assert out.report == "wrote index.html, read index.html"

    # Each turn reports its OWN actions — not the whole build's.
    out2 = build_steps.step({}, tmp_path, tools, cursor,
                            _reply(calls=[("list_files", {})]))
    assert out2.report == "listed files"


def test_a_failed_tool_call_reports_its_reason(tmp_path, tools):
    """"failed" alone sends the watcher to the logs; the reason is the whole value of the line."""
    cursor = _cursor()
    build_steps.step({}, tmp_path, tools, cursor, {})
    out = build_steps.step({}, tmp_path, tools, cursor,
                           _reply(calls=[("read_file", {"path": "nope.js"})]))
    assert out.report.startswith("read nope.js — failed: ")
    assert len(out.report) > len("read nope.js — failed: ")


def test_the_opening_turn_says_the_prompt_went_out(tmp_path, tools):
    cursor = _cursor()
    out = build_steps.step({}, tmp_path, tools, cursor, {})
    assert out.report == "sent the prompt"


def test_a_turn_with_no_tool_call_says_so(tmp_path, tools):
    cursor = _cursor()
    build_steps.step({}, tmp_path, tools, cursor, {})
    out = build_steps.step({}, tmp_path, tools, cursor, _reply(content="thinking out loud"))
    assert out.report == "no tool call — asked again"


def test_a_long_done_summary_is_cut_on_a_word_boundary(tmp_path, tools):
    cursor = _cursor()
    build_steps.step({}, tmp_path, tools, cursor, {})
    out = build_steps.step({}, tmp_path, tools, cursor, _reply(
        calls=[("done", {"summary": "built the thing " * 60})]))
    assert isinstance(out, build_steps.Done)
    assert cursor.summary.endswith("…")
    assert not cursor.summary.rstrip("…").endswith(" ")
    # cut BETWEEN words, never through one
    assert cursor.summary.rstrip("…").split()[-1] in ("built", "the", "thing")


def test_truncated_reply_is_told_nothing_was_saved(tmp_path, tools):
    cursor = _cursor()
    build_steps.step({}, tmp_path, tools, cursor, {})
    build_steps.step({}, tmp_path, tools, cursor,
                     _reply(content="const x = ",
                            usage={"completion_tokens": build_steps.MAX_TOKENS}))
    assert "cut off" in cursor.history[-1]["content"]


def test_turn_cap_ends_the_build(tmp_path, tools):
    cursor = _cursor()
    build_steps.step({}, tmp_path, tools, cursor, {})
    cursor.turn = build_steps.MAX_TURNS - 1
    out = build_steps.step({}, tmp_path, tools, cursor, _reply(content="thinking"))
    assert isinstance(out, build_steps.Done) and "cap" in out.report


def test_no_tool_call_is_nudged_not_failed(tmp_path, tools):
    cursor = _cursor()
    build_steps.step({}, tmp_path, tools, cursor, {})
    out = build_steps.step({}, tmp_path, tools, cursor, _reply(content="I will now write it."))
    assert isinstance(out, build_steps.Infer)
    assert "Keep going" in cursor.history[-1]["content"]


def test_generate_media_reaches_the_tool_and_its_path_reaches_the_transcript(tmp_path):
    """The model writes code against the path it gets back on the same turn, so the tool result has
    to carry it."""
    seen = {}

    def _spy(**kw):
        seen.update(kw)
        return {"ok": True, "path": "assets/goblin.glb", "status": "rendering"}

    cursor = _cursor()
    spy_tools = {"generate_media": _spy}
    build_steps.step({}, tmp_path, spy_tools, cursor, {})
    build_steps.step({}, tmp_path, spy_tools, cursor, _reply(calls=[
        ("generate_media", {"id": "goblin", "prompt": "a snarling goblin", "kind": "mesh"})]))
    assert seen == {"id": "goblin", "prompt": "a snarling goblin", "kind": "mesh"}
    assert "assets/goblin.glb" in cursor.history[-1]["content"]


# ── the seed ──────────────────────────────────────────────────────────────────
def test_seed_places_the_renderer_and_leaves_edits_alone(tmp_path, monkeypatch):
    """A game fetches nothing at runtime, so the renderer has to be in the folder before the model
    starts writing. A re-seed (a fix, a resumed build) must not overwrite what is there."""
    from maestro.codegen import staging
    monkeypatch.setattr(staging, "RUNTIME_DIR", tmp_path / "runtime")
    vendor = tmp_path / "runtime" / "vendor"
    vendor.mkdir(parents=True)
    (vendor / "three.module.js").write_text("// three")
    staging.seed_vendor(tmp_path)
    assert (tmp_path / "game" / "three.module.js").read_text() == "// three"

    (tmp_path / "game" / "index.html").write_text("<h1>hi</h1>")
    staging.seed_vendor(tmp_path)
    assert (tmp_path / "game" / "index.html").read_text() == "<h1>hi</h1>"
    assert sorted(p.name for p in (tmp_path / "game").iterdir()) == ["index.html", "three.module.js"]
    # The audit judges the game, not the renderer it imports.
    assert list(staging.game_files(tmp_path)) == ["index.html"]


# ── staging ───────────────────────────────────────────────────────────────────
def test_stage_copies_the_folder(tmp_path, monkeypatch):
    from maestro.codegen import staging
    runtime = tmp_path / "runtime"
    monkeypatch.setattr(staging, "RUNTIME_DIR", runtime)
    gd = tmp_path / "game"
    (gd / "assets").mkdir(parents=True)
    (gd / "index.html").write_text("<h1>hi</h1>")
    (gd / "assets" / "card.svg").write_text("<svg/>")
    (gd / "_transcript.jsonl").write_text("noise")
    assert stage_for_play(tmp_path, "abc123") == "games/abc123/index.html"
    assert (runtime / "games" / "abc123" / "index.html").read_text() == "<h1>hi</h1>"
    assert (runtime / "games" / "abc123" / "assets" / "card.svg").exists()
    assert not (runtime / "games" / "abc123" / "_transcript.jsonl").exists()


def test_stage_replaces_a_previous_stage(tmp_path, monkeypatch):
    from maestro.codegen import staging
    runtime = tmp_path / "runtime"
    monkeypatch.setattr(staging, "RUNTIME_DIR", runtime)
    stale = runtime / "games" / "abc123"
    stale.mkdir(parents=True)
    (stale / "gone.js").write_text("old")
    gd = tmp_path / "game"
    gd.mkdir()
    (gd / "index.html").write_text("new")
    stage_for_play(tmp_path, "abc123")
    assert not (stale / "gone.js").exists()


def test_cursor_survives_a_reload(tmp_path):
    from maestro.codegen import build_state
    build_state.save(tmp_path, _cursor(phase="audit", turn=7, compacted=2))
    back = build_state.load(tmp_path)
    assert (back.phase, back.turn, back.compacted) == ("audit", 7, 2)
