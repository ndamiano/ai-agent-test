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
    return build_tools(RunState(tmp_path), "b1")


def _cursor(**kw):
    return BuildCursor(build_id="b1", **kw)


def _done(tmp_path, tools, cursor):
    """Spend the one `done` the build answers instead of accepting, so a test about what the SECOND
    done does starts from the state a real build reaches."""
    build_steps.step({}, tmp_path, tools, cursor, _reply(code='done(summary="first pass")'))


def _reply(content="", code=None, usage=None, programs=None):
    """A turn: the model's reply carrying one `python` call whose `code` is the program."""
    msg = {"role": "assistant", "content": content}
    bodies = programs if programs is not None else ([code] if code is not None else [])
    if bodies:
        msg["tool_calls"] = [
            {"id": f"c{i}", "type": "function",
             "function": {"name": "python", "arguments": json.dumps({"code": b})}}
            for i, b in enumerate(bodies)]
    return {"choices": [{"message": msg}], "usage": usage or {}}


def _program(code, i, tag="c"):
    return {"id": f"{tag}{i}", "type": "function",
            "function": {"name": "python", "arguments": json.dumps({"code": code})}}


def _history():
    h = [{"role": "user", "content": "make a game"}]
    for i in range(6):
        h.append({"role": "assistant", "content": f"step {i}",
                  "tool_calls": [_program(f'write_file(path="f{i}.js", content="x")', i)]})
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


def test_the_first_turn_sends_the_request_and_nothing_else(tmp_path, tools):
    """The brief is for the human and the audit. Prepending its restated mechanics puts a second,
    more concrete instruction beside the request on every turn — the small-model failure mode."""
    cursor = _cursor()
    spec = {"request": "a card game", "design": {"look": "inky woodcut", "audio": "lute",
                                                 "mechanics": ["draw five cards a turn"]}}
    out = build_steps.step(spec, tmp_path, tools, cursor, {})
    assert isinstance(out, build_steps.Infer)
    assert out.messages[-1]["content"] == "a card game"
    assert [t["function"]["name"] for t in out.schemas] == ["python"]


def test_the_system_prompt_stays_the_measured_one(tmp_path, tools):
    """Every line here is read on every turn of every build. Additions regressed the artifact once
    already, so the prompt is pinned: the grid's six rules, plus the renderer and the media tool —
    the two things in the project the model cannot infer from a file listing."""
    cursor = _cursor()
    out = build_steps.step({"request": "a card game"}, tmp_path, tools, cursor, {})
    system = out.messages[0]["content"]
    assert "three.module.js" in system and "generate_media" in system
    # Measured 2026-08-22: the library closed three ledger items 6/6 against a control arm.
    assert "lib/input.js" in system
    assert "seeded generator" in system
    # Pinned to its measurement: 2/2 games sized the canvas with this line, 0/2 without.
    assert "sized to the window" in system
    # The functions are documented here, once per turn, rather than in seven schemas re-read with
    # every tool: 1,831 tokens of window against 266 (measured 2026-09-07).
    assert "check_syntax" in system and "read_file(path" in system
    assert "nothing you bind survives" in system
    # assets.json is written by the platform, so naming it here would invite the model to write it.
    assert "assets.json" not in system


def test_fix_note_replaces_the_request(tmp_path, tools):
    cursor = _cursor(request="the player cannot move")
    out = build_steps.step({"request": "a card game"}, tmp_path, tools, cursor, {})
    assert "the player cannot move" in out.messages[-1]["content"]


def test_the_first_done_is_answered_not_accepted(tmp_path, tools):
    """A model's own bar for playable is that it wrote the files. One bounded turn asks it to look
    once more, and is satisfiable by naming nothing."""
    cursor = _cursor()
    build_steps.step({}, tmp_path, tools, cursor, {})
    out = build_steps.step({}, tmp_path, tools, cursor, _reply(code='done(summary="shipped")'))
    assert isinstance(out, build_steps.Infer)
    assert cursor.finished is False and cursor.done_nudged is True
    assert cursor.history[-1]["role"] == "tool"
    assert build_steps._DONE_NUDGE in cursor.history[-1]["content"]


def test_the_nudge_carries_what_the_art_audit_found(tmp_path, tools):
    """The one place the build already asks what is unfinished. A build that asked for art and drew
    the game without it has no other moment to hear so — generate_media answers with a path and
    never learns whether the path was used."""
    game = tmp_path / "game"
    game.mkdir(parents=True, exist_ok=True)
    (game / "assets.json").write_text(
        '{"images": [{"id": "ghost", "file": "assets/ghost.png", "prompt": "a ghost"}]}')
    (game / "game.js").write_text("ctx.fillRect(0, 0, 32, 32)")

    cursor = _cursor()
    build_steps.step({}, tmp_path, tools, cursor, {})
    build_steps.step({}, tmp_path, tools, cursor, _reply(code='done(summary="shipped")'))

    nudge = cursor.history[-1]["content"]
    assert build_steps._DONE_NUDGE in nudge
    assert "ghost" in nudge


def test_done_ends_the_build(tmp_path, tools):
    cursor = _cursor()
    build_steps.step({}, tmp_path, tools, cursor, {})
    _done(tmp_path, tools, cursor)
    out = build_steps.step({}, tmp_path, tools, cursor, _reply(code='done(summary="shipped")'))
    assert isinstance(out, build_steps.Done)
    assert cursor.finished is True and "shipped" in out.report


def test_the_nudge_is_asked_once_not_every_done(tmp_path, tools):
    """Work happens between the two dones. The second one must be accepted whatever came between —
    a bar the model cannot get past grinds to the step cap."""
    cursor = _cursor()
    build_steps.step({}, tmp_path, tools, cursor, {})
    _done(tmp_path, tools, cursor)
    build_steps.step({}, tmp_path, tools, cursor,
                     _reply(code='write_file(path="help.html", content="controls")'))
    out = build_steps.step({}, tmp_path, tools, cursor,
                           _reply(code='done(summary="added the controls screen")'))
    assert isinstance(out, build_steps.Done)


def test_tool_call_lands_on_disk_and_continues(tmp_path, tools):
    cursor = _cursor()
    build_steps.step({}, tmp_path, tools, cursor, {})
    out = build_steps.step({}, tmp_path, tools, cursor,
                           _reply(code='write_file(path="index.html", content="<h1>hi</h1>")'))
    assert isinstance(out, build_steps.Infer)
    assert (tmp_path / "game" / "index.html").read_text() == "<h1>hi</h1>"
    assert cursor.history[-1]["role"] == "tool"


def test_the_step_report_says_what_the_turn_did(tmp_path, tools):
    """The feed line is the only view a watcher has of a running build, so it names the files the
    turn touched rather than counting turns."""
    cursor = _cursor()
    build_steps.step({}, tmp_path, tools, cursor, {})
    out = build_steps.step({}, tmp_path, tools, cursor, _reply(code=(
        'write_file(path="index.html", content="<h1>hi</h1>")\n'
        'read_file(path="index.html")')))
    assert out.report == "wrote index.html, read index.html"

    # Each turn reports its OWN actions — not the whole build's.
    out2 = build_steps.step({}, tmp_path, tools, cursor, _reply(code="list_files()"))
    assert out2.report == "listed files"


def test_the_report_folds_a_program_that_touched_many_files(tmp_path, tools):
    """A program may write a dozen files and ask for twenty pictures. The feed is read by a person,
    so the line says how many, not all of them."""
    cursor = _cursor()
    build_steps.step({}, tmp_path, tools, cursor, {})
    out = build_steps.step({}, tmp_path, tools, cursor, _reply(code=(
        "for i in range(6):\n"
        '    write_file(path=f"f{i}.js", content="x")')))
    assert out.report == "wrote 6 files"


def test_a_failed_tool_call_reports_its_reason(tmp_path, tools):
    """"failed" alone sends the watcher to the logs; the reason is the whole value of the line."""
    cursor = _cursor()
    build_steps.step({}, tmp_path, tools, cursor, {})
    out = build_steps.step({}, tmp_path, tools, cursor,
                           _reply(code='read_file(path="nope.js")'))
    assert out.report == "read nope.js — failed"


def test_the_opening_turn_says_the_prompt_went_out(tmp_path, tools):
    cursor = _cursor()
    out = build_steps.step({"request": "make a snake game"}, tmp_path, tools, cursor, {})
    assert out.report == "sent the prompt: make a snake game"


def test_the_opening_line_of_a_fix_carries_the_note(tmp_path, tools):
    cursor = _cursor(kind="fix", request="the snake never dies")
    out = build_steps.step({}, tmp_path, tools, cursor, {})
    assert out.report == "sent the fix note: the snake never dies"


def test_a_long_prompt_is_clipped_in_the_feed(tmp_path, tools):
    cursor = _cursor()
    out = build_steps.step({"request": "word " * 200}, tmp_path, tools, cursor, {})
    assert out.report.startswith("sent the prompt: word word")
    assert out.report.endswith("…") and len(out.report) < 340


def test_a_turn_with_no_tool_call_says_so(tmp_path, tools):
    cursor = _cursor()
    build_steps.step({}, tmp_path, tools, cursor, {})
    out = build_steps.step({}, tmp_path, tools, cursor, _reply(content="thinking out loud"))
    assert out.report == "no tool call — asked again"


def test_no_result_re_asks_instead_of_inventing_an_empty_turn(tmp_path, tools):
    """A resume, or a reaper re-drive, has no turn to apply. Treating that as a turn that answered
    with nothing scolds the model for a reply it never sent, and burns a turn against the cap."""
    cursor = _cursor()
    build_steps.step({}, tmp_path, tools, cursor, {})
    build_steps.step({}, tmp_path, tools, cursor,
                     _reply(code='write_file(path="index.html", content="<h1>hi</h1>")'))
    before, turn = list(cursor.history), cursor.turn

    out = build_steps.step({}, tmp_path, tools, cursor, None)

    assert isinstance(out, build_steps.Infer)
    assert cursor.history == before and cursor.turn == turn
    assert cursor.no_call_streak == 0


def test_a_long_done_summary_is_cut_on_a_word_boundary(tmp_path, tools):
    cursor = _cursor()
    build_steps.step({}, tmp_path, tools, cursor, {})
    _done(tmp_path, tools, cursor)
    out = build_steps.step({}, tmp_path, tools, cursor, _reply(
        code='done(summary="' + "built the thing " * 60 + '")'))
    assert isinstance(out, build_steps.Done)
    assert cursor.summary.endswith("…")
    assert not cursor.summary.rstrip("…").endswith(" ")
    # cut BETWEEN words, never through one
    assert cursor.summary.rstrip("…").split()[-1] in ("built", "the", "thing")


def test_a_truncated_reply_is_thrown_away_and_the_turn_sent_again(tmp_path, tools):
    """A reply that ran out of room is an inference failure, not a transcript one: the same prompt
    resampled produces a normal turn (measured 2 failures in 36 replays of six such positions), and
    telling the model it was cut off makes the next turn 7x bigger. So nothing is kept from it."""
    cursor = _cursor()
    build_steps.step({}, tmp_path, tools, cursor, {})
    before = [dict(m) for m in cursor.history]
    outcome = build_steps.step({}, tmp_path, tools, cursor,
                               _reply(content="const x = ",
                                      usage={"completion_tokens": cursor.out_cap}))
    assert isinstance(outcome, build_steps.Infer)
    assert cursor.history == before, "the cut-off reply must leave no trace in the transcript"
    assert not any("cut off" in str(m.get("content", "")) for m in cursor.history)


def test_the_build_gives_up_after_repeated_cut_offs(tmp_path, tools):
    cursor = _cursor()
    build_steps.step({}, tmp_path, tools, cursor, {})
    for _ in range(build_steps._NO_CALL_GIVE_UP):
        outcome = build_steps.step({}, tmp_path, tools, cursor,
                                   _reply(content="const x = ",
                                          usage={"completion_tokens": cursor.out_cap}))
    assert isinstance(outcome, build_steps.Done)
    assert "cut off" in outcome.report


def _raw_reply(raw_args):
    """A reply whose tool-call arguments are the literal string given — what a call cut off at the
    output cap looks like, which `_reply` (which serializes a dict) can never produce."""
    return {"choices": [{"message": {"role": "assistant", "content": "",
                                     "tool_calls": [{"id": "c0", "type": "function",
                                                     "function": {"name": "python",
                                                                  "arguments": raw_args}}]}}],
            "usage": {}}


def test_a_call_cut_off_mid_argument_says_so_and_writes_nothing(tmp_path, tools):
    """The measured build-killer: a 64 KB write_file cut at the output cap parses to no arguments,
    which reached the model as `KeyError: 'path'` — so it resent the same oversized call until the
    server itself refused it."""
    cursor = _cursor()
    build_steps.step({}, tmp_path, tools, cursor, {})
    build_steps.step({}, tmp_path, tools, cursor,
                     _raw_reply('{"code":"write_file(path=\\"story.js\\", content=\\"const '))
    said = cursor.history[-1]["content"]
    assert "output token limit" in said and "nothing ran" in said
    assert "KeyError" not in said
    assert not (tmp_path / "game" / "story.js").exists()


def test_a_call_that_takes_no_arguments_is_not_read_as_cut_off(tmp_path, tools):
    """`{}` is a whole argument list, not a truncated one — list_files carries exactly that."""
    cursor = _cursor()
    build_steps.step({}, tmp_path, tools, cursor, {})
    build_steps.step({}, tmp_path, tools, cursor,
                     _raw_reply(json.dumps({"code": "print(len(list_files()))"})))
    said = cursor.history[-1]["content"]
    assert "output token limit" not in said
    assert said.startswith("0")


def test_a_turn_the_server_refused_is_not_a_turn_that_said_nothing(tmp_path, tools):
    """A 500 from the server's own tool-call parser reaches the driver as an error and no message.
    Told only 'that reply contained no tool call', the model resends what earned the 500."""
    cursor = _cursor()
    build_steps.step({}, tmp_path, tools, cursor, {})
    out = build_steps.step({}, tmp_path, tools, cursor, {},
                           error="Status 500: Failed to parse tool call arguments as JSON")
    assert isinstance(out, build_steps.Infer)
    said = cursor.history[-1]["content"]
    assert "could not read your last reply" in said
    assert "Failed to parse tool call arguments" in said
    assert "smaller pieces" in said
    assert "no tool call" not in said


def test_a_refused_error_is_clipped_before_it_reaches_the_transcript(tmp_path, tools):
    """The 500 body carries the whole oversized argument back — 48 KB of it, in one build."""
    cursor = _cursor()
    build_steps.step({}, tmp_path, tools, cursor, {})
    build_steps.step({}, tmp_path, tools, cursor, {}, error="Status 500: " + "x" * 50_000)
    assert len(cursor.history[-1]["content"]) < 1000


def test_a_build_the_server_keeps_refusing_gives_up(tmp_path, tools):
    cursor = _cursor()
    build_steps.step({}, tmp_path, tools, cursor, {})
    out = None
    for _ in range(build_steps._NO_CALL_GIVE_UP):
        out = build_steps.step({}, tmp_path, tools, cursor, {}, error="Status 500: nope")
    assert isinstance(out, build_steps.Done) and "could not read" in out.report


def test_a_landed_turn_after_a_refusal_clears_the_streak(tmp_path, tools):
    cursor = _cursor()
    build_steps.step({}, tmp_path, tools, cursor, {})
    build_steps.step({}, tmp_path, tools, cursor, {}, error="Status 500: nope")
    build_steps.step({}, tmp_path, tools, cursor, _reply(code="list_files()"))
    assert cursor.no_call_streak == 0


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


_QUOTED = "function f() {\n  el.innerHTML = '<div style=\"color:#888\">?</div>';\n}\n"


def test_a_read_reaches_the_program_as_the_file_itself(tmp_path, tools):
    """The measured loop: `\\"` copied out of a serialized read into old_text matches nothing. A
    read is a VALUE now, so nothing encodes it on the way — and the program proves it by editing
    against text it read a moment earlier."""
    (tmp_path / "game" / "game.js").write_text(_QUOTED, encoding="utf-8")
    cursor = _cursor()
    build_steps.step({}, tmp_path, tools, cursor, {})
    out = build_steps.step({}, tmp_path, tools, cursor, _reply(code=(
        'src = read_file(path="game.js")\n'
        'line = src.splitlines()[1]\n'
        'print(edit_file(path="game.js", old_text=line, new_text="  el.innerHTML = \'x\';"))\n'
        'print("edited")')))
    assert out.report == "read game.js, edited game.js"
    assert "'x'" in (tmp_path / "game" / "game.js").read_text()


def test_only_what_the_program_prints_reaches_the_transcript(tmp_path, tools):
    """A read costs the window nothing unless the model chooses to spend it. This is what keeps a
    build's transcript small enough to never compact: 37 turns and 19 reads left the prompt at 72K
    of 131K (measured 2026-09-07)."""
    (tmp_path / "game" / "big.js").write_text("y" * 50_000, encoding="utf-8")
    cursor = _cursor()
    build_steps.step({}, tmp_path, tools, cursor, {})
    build_steps.step({}, tmp_path, tools, cursor, _reply(code=(
        'src = read_file(path="big.js")\n'
        'print("big.js is", len(src), "chars")')))
    said = cursor.history[-1]["content"]
    assert "big.js is 50000 chars" in said
    assert "y" * 200 not in said


def test_a_whole_file_reaches_the_program_however_long(tmp_path, tools):
    """The ceiling on a read was a context guard, and a program's read never touches the context.
    Left in place it made a partial read look like a whole one: the model counted occurrences over
    43% of a file and read the zeroes as missing edits (measured 2026-09-07)."""
    (tmp_path / "game" / "big.js").write_text("z" * 300_000, encoding="utf-8")
    cursor = _cursor()
    build_steps.step({}, tmp_path, tools, cursor, {})
    build_steps.step({}, tmp_path, tools, cursor,
                     _reply(code='print(len(read_file(path="big.js")))'))
    assert cursor.history[-1]["content"].startswith("300000")


def test_a_failed_call_returns_its_error_and_the_program_carries_on(tmp_path, tools):
    """A raise abandons every statement after it: one bad edit lost ten good ones and the model
    had to work out how far it got (measured 2026-09-07: 5 of 16 applied)."""
    cursor = _cursor()
    build_steps.step({}, tmp_path, tools, cursor, {})
    build_steps.step({}, tmp_path, tools, cursor, _reply(code=(
        'print("first:", read_file(path="nope.js")[:6])\n'
        'write_file(path="after.js", content="// still ran")\n'
        'print("second write happened")')))
    said = cursor.history[-1]["content"]
    assert "first: ERROR" in said and "second write happened" in said
    assert (tmp_path / "game" / "after.js").exists()


def test_the_result_lists_the_calls_and_names_the_ones_that_failed(tmp_path, tools):
    """With failures returned rather than raised, a program that ignores what a call gave back
    would never learn it failed — so every result carries the ledger."""
    cursor = _cursor()
    build_steps.step({}, tmp_path, tools, cursor, {})
    build_steps.step({}, tmp_path, tools, cursor, _reply(code=(
        'write_file(path="a.js", content="1")\n'
        'edit_file(path="a.js", old_text="nope", new_text="x")')))
    said = cursor.history[-1]["content"]
    assert "what the program called" in said
    assert "write_file a.js" in said
    assert "edit_file a.js  — FAILED" in said
    assert "1 of those FAILED" in said


def test_a_program_that_reaches_outside_the_session_is_refused_before_it_runs(tmp_path, tools):
    """The static check is not the boundary — the seccomp filter is — but it refuses the obvious
    reach with a sentence the model can act on, and says plainly that nothing ran."""
    cursor = _cursor()
    build_steps.step({}, tmp_path, tools, cursor, {})
    out = build_steps.step({}, tmp_path, tools, cursor, _reply(code=(
        'import subprocess\n'
        'write_file(path="never.js", content="x")')))
    said = cursor.history[-1]["content"]
    assert "no module 'subprocess'" in said and "nothing in the program ran" in said
    assert not (tmp_path / "game" / "never.js").exists()
    assert out.report.startswith("program refused:")


def test_only_the_first_program_of_a_reply_runs(tmp_path, tools):
    """Two programs in one reply means the second wants to act on the first's output, which it has
    not seen. Running both would be acting on a result the model never read."""
    cursor = _cursor()
    build_steps.step({}, tmp_path, tools, cursor, {})
    build_steps.step({}, tmp_path, tools, cursor, _reply(programs=[
        'write_file(path="one.js", content="1")',
        'write_file(path="two.js", content="2")']))
    assert (tmp_path / "game" / "one.js").exists()
    assert not (tmp_path / "game" / "two.js").exists()
    assert "Only the first program ran" in cursor.history[-1]["content"]


def _fail(tmp_path, tools, cursor, code, times):
    for _ in range(times):
        build_steps.step({}, tmp_path, tools, cursor, _reply(code=code))
    return cursor.history[-1]["content"]


def test_an_identical_failing_call_is_told_it_is_repeating(tmp_path, tools):
    """The loop this ends: a failing edit resent byte for byte to the step cap."""
    cursor = _cursor()
    build_steps.step({}, tmp_path, tools, cursor, {})
    prog = 'edit_file(path="game.js", old_text="a", new_text="b")'
    assert "sent this exact program" not in _fail(tmp_path, tools, cursor, prog, 1)
    assert "sent this exact program 2 times" in _fail(tmp_path, tools, cursor, prog, 1)
    assert "sent this exact program 4 times" in _fail(tmp_path, tools, cursor, prog, 2)


def test_a_succeeding_call_between_retries_does_not_reset_the_count(tmp_path, tools):
    """The measured loop: read → failing edit → read → the SAME failing edit, twelve times, every
    one counted as the first because the read in between succeeded."""
    (tmp_path / "game" / "game.js").write_text("hello\n", encoding="utf-8")
    cursor = _cursor()
    build_steps.step({}, tmp_path, tools, cursor, {})
    prog = 'edit_file(path="game.js", old_text="nope", new_text="b")'
    for _ in range(3):
        _fail(tmp_path, tools, cursor, 'read_file(path="game.js")', 1)
        content = _fail(tmp_path, tools, cursor, prog, 1)
    assert "sent this exact program 3 times" in content


def test_the_repeat_note_keeps_the_reason_the_call_failed(tmp_path, tools):
    cursor = _cursor()
    build_steps.step({}, tmp_path, tools, cursor, {})
    content = _fail(tmp_path, tools, cursor, 'print(read_file(path="nope.js"))', 2)
    assert "no such file" in content and "2 times" in content


def test_a_changed_argument_is_not_a_repeat(tmp_path, tools):
    cursor = _cursor()
    build_steps.step({}, tmp_path, tools, cursor, {})
    _fail(tmp_path, tools, cursor, 'read_file(path="nope.js")', 1)
    content = _fail(tmp_path, tools, cursor, 'read_file(path="other.js")', 1)
    assert "sent this exact program" not in content


def test_the_repeat_note_stays_out_of_the_build_feed(tmp_path, tools):
    """The feed line is one clipped sentence."""
    cursor = _cursor()
    build_steps.step({}, tmp_path, tools, cursor, {})
    prog = 'read_file(path="nope.js")'
    build_steps.step({}, tmp_path, tools, cursor, _reply(code=prog))
    out = build_steps.step({}, tmp_path, tools, cursor, _reply(code=prog))
    assert "sent this exact program" not in out.report


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
    build_steps.step({}, tmp_path, spy_tools, cursor, _reply(code=(
        'print(generate_media(id="goblin", subject="a snarling goblin", kind="mesh"))')))
    assert seen == {"id": "goblin", "subject": "a snarling goblin", "kind": "mesh"}
    assert "assets/goblin.glb" in cursor.history[-1]["content"]


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
    assert sorted(p.name for p in (tmp_path / "game").iterdir()) == ["index.html", "lib", "three.module.js"]


def test_seed_places_the_world_loader(tmp_path):
    """The world loader rides along with three.js, from the real vendor folder: a game fetches
    nothing at runtime, and the terrain shader is not something a build should be writing. The
    page the pipeline renders worlds with is not a game's, and does not go."""
    from maestro.codegen import staging
    staging.seed_vendor(tmp_path)
    game = tmp_path / "game"
    for name in ("world.js", "GLTFLoader.js", "BufferGeometryUtils.js"):
        assert (game / name).is_file()
    assert "export async function loadWorld" in (game / "world.js").read_text()
    assert not (game / "world_render.html").exists()


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


def test_seed_places_the_helper_library_and_leaves_edits_alone(tmp_path):
    from maestro.codegen import staging
    staging.seed_vendor(tmp_path)
    lib = tmp_path / "game" / "lib"
    vendored = sorted(p.name for p in (staging.RUNTIME_DIR / "vendor" / "lib").glob("*.js"))
    assert vendored and sorted(p.name for p in lib.iterdir()) == vendored
    edited = lib / vendored[0]
    edited.write_text("// edited")
    staging.seed_vendor(tmp_path)
    assert edited.read_text() == "// edited"


def _file_history(n=6):
    """A build's shape: a round that writes a file out, then a round that only looks at it."""
    h = [{"role": "user", "content": "make a game"}]
    for i in range(n):
        h.append({"role": "assistant", "content": f"step {i}",
                  "tool_calls": [_program(f'write_file(path="f{i}.js", content={"y" * 1500!r})', i)]})
        h.append({"role": "tool", "tool_call_id": f"c{i}", "content": "wrote it"})
        h.append({"role": "assistant", "content": f"look {i}",
                  "tool_calls": [_program(f'print(read_file(path="f{i}.js")[:20])', i, "r")]})
        h.append({"role": "tool", "tool_call_id": f"r{i}", "content": "yyyyyyyyyyyyyyyyyyyy"})
    return h


def _bodies(history):
    """Which messages still carry a whole 1500-char body, by index."""
    return [i for i, m in enumerate(history) if "y" * 1500 in json.dumps(m)]


def test_compact_stubs_the_oldest_bodies_only_until_the_tail_fits(tmp_path):
    (tmp_path / "game").mkdir()
    cursor = _cursor(history=_file_history())
    # Six files written, each in its own round. A budget two bodies short means the two OLDEST
    # writes lose their bytes and the four newest keep them.
    kept = build_steps.drop_read_only_rounds(cursor.history)
    keep = sum(len(json.dumps(m)) for m in kept[1:]) - 2 * 1400
    assert build_steps.compact(tmp_path, cursor, keep_chars=keep) > 0
    assert "whole project" in cursor.history[1]["content"]   # the map leads every compaction
    assert "the 1500 chars written to f0.js" in json.dumps(cursor.history[2])
    assert "the 1500 chars written to f1.js" in json.dumps(cursor.history[4])
    assert _bodies(cursor.history) == [6, 8, 10, 12]         # f2..f5, whole


def test_a_stubbed_program_still_says_what_the_model_did(tmp_path):
    """The round keeps its shape — the loop, the art asks, the order — and loses only bytes that
    are on disk. Without that the model edits code it no longer remembers writing."""
    (tmp_path / "game").mkdir()
    code = ('for name in ["a", "b"]:\n'
            '    generate_media(id=name, kind="sprite", subject=name, style="ink")\n'
            f'write_file(path="game.js", content={"y" * 1500!r})\n'
            'print("done")')
    cursor = _cursor(history=[{"role": "user", "content": "go"},
                              {"role": "assistant", "content": "", "tool_calls": [_program(code, 0)]},
                              {"role": "tool", "tool_call_id": "c0", "content": "done"}])
    build_steps.compact(tmp_path, cursor, keep_chars=700)
    left = build_steps.parse_args(
        cursor.history[-2]["tool_calls"][0]["function"]["arguments"])["code"]
    assert "generate_media(id=name" in left and "for name in" in left
    assert "y" * 100 not in left
    assert "the 1500 chars written to game.js" in left
    import ast
    ast.parse(left)                                          # still a program, not a ruin


def test_compact_keeps_only_the_newest_copy_of_each_file(tmp_path):
    (tmp_path / "game").mkdir()
    h = [{"role": "user", "content": "make a game"}]
    for k in range(3):
        h.append({"role": "assistant", "content": f"write {k}",
                  "tool_calls": [_program(f'write_file(path="f0.js", content={"y" * 1500!r})', k)]})
        h.append({"role": "tool", "tool_call_id": f"c{k}", "content": "ok"})
    cursor = _cursor(history=h)
    assert build_steps.compact(tmp_path, cursor, keep_chars=10_000_000) == 1
    assert _bodies(cursor.history) == [len(cursor.history) - 2]   # the newest write alone
    assert "read it again" not in json.dumps(cursor.history)


def test_an_edit_is_never_stubbed(tmp_path):
    """An edit is a delta, not a body: what it replaced and what it became is the only record of
    what the model changed since it wrote the file."""
    (tmp_path / "game").mkdir()
    wrote = f'write_file(path="f0.js", content={"y" * 3000!r})'
    edited = f'edit_file(path="f0.js", old_text={"a" * 900!r}, new_text={"b" * 900!r})'
    h = [{"role": "user", "content": "go"},
         {"role": "assistant", "content": "", "tool_calls": [_program(wrote, 0)]},
         {"role": "tool", "tool_call_id": "c0", "content": "ok"},
         {"role": "assistant", "content": "", "tool_calls": [_program(edited, 1)]},
         {"role": "tool", "tool_call_id": "c1", "content": "ok"}]
    cursor = _cursor(history=h)
    # A budget that the write's body alone overshoots: stubbing it is enough, and the edit's
    # delta is never a candidate however tight the budget gets.
    build_steps.compact(tmp_path, cursor, keep_chars=2500)
    assert "the 3000 chars written to f0.js" in json.dumps(cursor.history)
    assert "a" * 900 in json.dumps(cursor.history)


def test_a_body_built_at_runtime_is_left_alone(tmp_path):
    """Only a literal has a span to cut. A body the program assembled is not repeated anywhere —
    the program IS the record of how it was made."""
    (tmp_path / "game").mkdir()
    built = ('rows = [f"const x{i} = {i};" for i in range(400)]\n'
             'write_file(path="data.js", content="\\n".join(rows))')
    literal = f'write_file(path="other.js", content={"y" * 3000!r})'
    h = [{"role": "user", "content": "go"},
         {"role": "assistant", "content": "", "tool_calls": [_program(literal, 0)]},
         {"role": "tool", "tool_call_id": "c0", "content": "ok"},
         {"role": "assistant", "content": "", "tool_calls": [_program(built, 1)]},
         {"role": "tool", "tool_call_id": "c1", "content": "ok"}]
    cursor = _cursor(history=h)
    build_steps.compact(tmp_path, cursor, keep_chars=2500)
    assert "the 3000 chars written to other.js" in json.dumps(cursor.history)
    left = build_steps.parse_args(
        cursor.history[-2]["tool_calls"][0]["function"]["arguments"])["code"]
    assert left == built


def test_compact_is_a_noop_when_nothing_is_superseded_and_it_fits(tmp_path):
    (tmp_path / "game").mkdir()
    h = [{"role": "user", "content": "go"}]
    for i in range(2):
        h.append({"role": "assistant", "content": f"s{i}",
                  "tool_calls": [_program(f'write_file(path="f{i}.js", content="short")', i)]})
        h.append({"role": "tool", "tool_call_id": f"c{i}", "content": "ok"})
    cursor = _cursor(history=h)
    assert build_steps.compact(tmp_path, cursor, keep_chars=10_000_000) == 0


def test_compact_drops_rounds_only_when_trimming_is_not_enough(tmp_path):
    (tmp_path / "game").mkdir()
    (tmp_path / "game" / "f0.js").write_text("x")
    cursor = _cursor(history=_file_history())
    assert build_steps.compact(tmp_path, cursor, keep_chars=1500) > 0
    assert "f0.js" in cursor.history[1]["content"]           # re-grounded: rounds went
    assert len(cursor.history) < len(_file_history())


def test_compaction_replays_the_trim_from_the_turn_log(tmp_path):
    (tmp_path / "game").mkdir()
    cursor = _cursor(history=_file_history())
    build_steps.compact(tmp_path, cursor, keep_chars=8000)
    from maestro.codegen import turn_log
    record = json.loads(turn_log.path(tmp_path).read_text().splitlines()[-1])
    assert record["kind"] == "compact" and record["trimmed"] > 0 and record["dropped"] == 0
    assert turn_log._compacted(_file_history(), record) == cursor.history


def test_replies_cut_off_at_the_cap_count_toward_the_stall(tmp_path, tools):
    """A model looping on one oversized write burned every turn to the cap: the cut-off branch never
    advanced the stall streak, so the give-up that ends every other no-call loop never fired."""
    cursor = _cursor()
    build_steps.step({}, tmp_path, tools, cursor, {})
    out = None
    for _ in range(build_steps._NO_CALL_GIVE_UP):
        out = build_steps.step({}, tmp_path, tools, cursor,
                               _reply(content="const x = ",
                                      usage={"completion_tokens": cursor.out_cap}))
    assert isinstance(out, build_steps.Done) and "stalled" in out.report


def test_a_turn_re_sent_forever_ends_the_build(tmp_path, tools):
    """A re-drive with nothing to apply enqueues a fresh job each time; a worker that dies on every
    one of them would otherwise re-drive without end."""
    cursor = _cursor()
    build_steps.step({}, tmp_path, tools, cursor, {})
    out = None
    for _ in range(build_steps._REDRIVE_GIVE_UP):
        out = build_steps.step({}, tmp_path, tools, cursor, None)
    assert isinstance(out, build_steps.Done) and "never ran" in out.report


def test_the_last_nudge_never_tells_a_fix_to_write_a_fresh_page(tmp_path, tools):
    """On a fix or a later stage the game already has its index.html; telling the model to write a
    minimal one would overwrite the finished game."""
    (tmp_path / "game" / "index.html").write_text("<h1>done</h1>")
    cursor = _cursor()
    build_steps.step({}, tmp_path, tools, cursor, {})
    for _ in range(3):
        build_steps.step({}, tmp_path, tools, cursor, _reply(content="hmm"))
    said = cursor.history[-1]["content"]
    assert "index.html" not in said and "edits it" in said
    assert (tmp_path / "game" / "index.html").read_text() == "<h1>done</h1>"


def test_a_misnamed_argument_is_named_back(tmp_path, tools):
    """Seen in prod: `read — failed: KeyError: 'path'` for a read sent as `file`. The model copies
    exact feedback, so the error names the argument it needs and the ones it sent."""
    cursor = _cursor()
    build_steps.step({}, tmp_path, tools, cursor, {})
    build_steps.step({}, tmp_path, tools, cursor,
                     _reply(code='print(read_file(file="lib/input.js"))'))
    said = cursor.history[-1]["content"]
    assert "KeyError" not in said
    assert "needs the argument 'path'" in said and "file" in said


def test_the_output_cap_is_the_ceiling_until_a_turn_overruns_it(tmp_path, tools, monkeypatch):
    """p99 of turns that produced a tool call is 22,138 tokens, so the first attempt is capped there
    rather than at the whole window — what a runaway costs is what the cap bounds. A turn that
    genuinely needs more overruns, and the retry is given everything the window has left."""
    monkeypatch.setattr(build_steps, "_n_ctx", lambda: 131_072)
    cursor = _cursor()
    build_steps.step({}, tmp_path, tools, cursor, {})
    assert cursor.out_cap == build_steps._OUT_CAP

    outcome = build_steps.step({}, tmp_path, tools, cursor,
                               _reply(content="const x = ",
                                      usage={"completion_tokens": cursor.out_cap}))
    assert isinstance(outcome, build_steps.Infer)
    assert cursor.out_cap > build_steps._OUT_CAP, "the retry gets the whole window"


def test_a_turn_that_merely_called_no_tool_does_not_earn_the_whole_window(tmp_path, tools,
                                                                          monkeypatch):
    """Only an overrun asks for more room. The ordinary no-tool-call nudge shares the same streak
    counter, and keying the ceiling off that would hand the whole window to turns that never
    needed it."""
    monkeypatch.setattr(build_steps, "_n_ctx", lambda: 131_072)
    cursor = _cursor()
    build_steps.step({}, tmp_path, tools, cursor, {})
    build_steps.step({}, tmp_path, tools, cursor,
                     _reply(content="thinking out loud, no call", usage={"completion_tokens": 40}))
    assert cursor.out_cap == build_steps._OUT_CAP


def test_the_cap_never_exceeds_what_the_window_has_left(tmp_path, tools, monkeypatch):
    monkeypatch.setattr(build_steps, "_n_ctx", lambda: 131_072)
    cursor = _cursor()
    build_steps.step({}, tmp_path, tools, cursor, {})
    cursor.logged = len(cursor.history)
    build_steps.step({}, tmp_path, tools, cursor,
                     _reply(code=f'write_file(path="a.js", content={"z" * 3000!r})',
                            usage={"prompt_tokens": 110_000}))
    # The last counted prompt plus this round (a 3K write and its result), never the whole
    # transcript re-estimated: a transcript of 110K tokens is well over 330K chars.
    assert 131_072 - 110_000 - 1200 < cursor.out_cap < 131_072 - 110_000 - 1000


def test_compaction_fires_when_the_window_has_less_than_the_room_left(tmp_path, tools, monkeypatch):
    monkeypatch.setattr(build_steps, "_n_ctx", lambda: 131_072)
    cursor = _cursor()
    build_steps.step({}, tmp_path, tools, cursor, {})
    cursor.history = _file_history()
    cursor.logged = len(cursor.history)      # every round below is one the server already counted
    cursor.prompt_tokens = 131_072 - build_steps._COMPACT_ROOM - 1
    build_steps._infer(tmp_path, cursor)
    assert cursor.compacted == 0
    cursor.prompt_tokens = 131_072 - build_steps._COMPACT_ROOM + 1
    build_steps._infer(tmp_path, cursor)
    assert cursor.compacted == 1


def test_the_compaction_note_carries_the_code_map(tmp_path):
    (tmp_path / "game" / "systems").mkdir(parents=True)
    (tmp_path / "game" / "systems" / "combat.js").write_text(
        "export function resolveCombat(a, d) {\n  return 1;\n}\n")
    cursor = _cursor(history=_file_history(n=2))
    build_steps.compact(tmp_path, cursor, keep_chars=1000)
    note = cursor.history[1]["content"]
    assert "systems/combat.js (4 lines)" in note and "  1-3  resolveCombat(a, d)" in note
    assert "offset and lines" in note


def test_a_program_with_hundreds_of_calls_reports_a_readable_ledger(tmp_path, tools):
    """The ledger rides every result, and a program may ask for a hundred pictures. Listing every
    one would cost more window than the output it is annotating."""
    cursor = _cursor()
    build_steps.step({}, tmp_path, tools, cursor, {})
    build_steps.step({}, tmp_path, tools, cursor, _reply(code=(
        "for i in range(60):\n"
        '    write_file(path=f"f{i}.js", content="x")')))
    said = cursor.history[-1]["content"]
    assert "… 20 more …" in said
    assert said.count("write_file") == 40


def test_the_prompt_still_says_how_to_ask_for_art(tmp_path, tools):
    """The art direction used to ride generate_media's schema. With one tool the schema is gone,
    so the same guidance has to be in the prompt — the `details` shape is not guessable, and the
    style phrase is what keeps one game's art one game's art."""
    cursor = _cursor()
    out = build_steps.step({"request": "a card game"}, tmp_path, tools, cursor, {})
    system = out.messages[0]["content"]
    for kind in ("sprite", "actor", "tile", "scene", "mesh"):
        assert f'"{kind}"' in system
    assert "facings" in system and "anims" in system and "body plan" in system
    assert "ONE style phrase" in system


def test_the_feed_line_folds_failures_too(tmp_path, tools):
    """A program whose twenty-two art asks all fail is one event, not twenty-two: naming each one
    made a 500-character feed line out of a turn a watcher reads at a glance."""
    cursor = _cursor()
    build_steps.step({}, tmp_path, tools, cursor, {})
    out = build_steps.step({}, tmp_path, tools, cursor, _reply(code=(
        "for i in range(22):\n"
        '    edit_file(path=f"gone{i}.js", old_text="a", new_text="b")')))
    assert out.report == "edited 22 files — all failed"


def _fat_history(rounds=40, body=6000):
    h = [{"role": "user", "content": "make a game"}]
    for i in range(rounds):
        h.append({"role": "assistant", "content": f"step {i}",
                  "tool_calls": [_program(f'write_file(path="f{i}.js", content={"z" * body!r})', i)]})
        h.append({"role": "tool", "tool_call_id": f"c{i}", "content": "wrote f%d.js" % i})
    return h


def test_compaction_fires_on_the_round_that_jumps_the_window(tmp_path, tools, monkeypatch):
    """The trigger reads the prompt about to be SENT, not the last one the server counted. A single
    round big enough to cross the window lands between two counts, and reading only the stale count
    left the transcript untrimmed while the server refused it."""
    monkeypatch.setattr(build_steps, "_n_ctx", lambda: 131_072)
    cursor = _cursor()
    build_steps.step({}, tmp_path, tools, cursor, {})
    cursor.history = _fat_history()
    cursor.logged = 1
    cursor.prompt_tokens = 100_000          # comfortably under the trigger on its own
    build_steps._infer(tmp_path, cursor)    # ...but the rounds since add well over the room left
    assert cursor.compacted == 1


def test_a_prompt_over_the_window_is_trimmed_and_re_sent(tmp_path, tools, monkeypatch):
    """Measured 2026-09-08 (run 90a89ba593ee): the server refused the PROMPT for its size and the
    driver answered as though the REPLY was unreadable — a note that was false and that made the
    next prompt bigger, three identical refusals apart. A prompt-side refusal compacts instead."""
    monkeypatch.setattr(build_steps, "_n_ctx", lambda: 131_072)
    cursor = _cursor()
    build_steps.step({}, tmp_path, tools, cursor, {})
    cursor.history = _fat_history()
    cursor.logged = 1
    out = build_steps.step({}, tmp_path, tools, cursor, None,
                           error='Status 400: {"error":{"code":"context_length_exceeded",'
                                 '"message":"prepared prompt has 131453 tokens, exceeding Engine '
                                 'max_context 131072"}}')
    assert cursor.compacted == 1
    assert isinstance(out, build_steps.Infer)
    assert not any("could not read your last reply" in str(m.get("content"))
                   for m in cursor.history)


def test_the_build_gives_up_when_the_prompt_cannot_be_trimmed(tmp_path, tools, monkeypatch):
    """Nothing left to trim and the prompt still over: the build stops rather than re-sending a
    prompt the server will refuse forever."""
    monkeypatch.setattr(build_steps, "_n_ctx", lambda: 131_072)
    cursor = _cursor()
    build_steps.step({}, tmp_path, tools, cursor, {})
    out = build_steps.step({}, tmp_path, tools, cursor, None,
                           error='Status 400: max_tokens must be positive')
    assert isinstance(out, build_steps.Done)
    assert "nothing is left to trim" in out.report


def test_the_cap_stays_positive_when_the_window_is_full(tmp_path, tools, monkeypatch):
    """The cap is what the window has left, and a full window is what compaction is for: after the
    trim there is room for a reply again."""
    monkeypatch.setattr(build_steps, "_n_ctx", lambda: 131_072)
    cursor = _cursor()
    build_steps.step({}, tmp_path, tools, cursor, {})
    cursor.history = _fat_history()
    cursor.logged = 1
    cursor.prompt_tokens = 130_000
    build_steps._infer(tmp_path, cursor)
    assert cursor.out_cap > 0


def test_the_compaction_note_says_what_is_already_read_and_unchanged(tmp_path, tools, monkeypatch):
    """The whole point of the block: the model is cut back to a code map and then goes and reads
    the same files again (measured 2026-09-08: 90 of 95 post-compaction reads)."""
    monkeypatch.setattr(build_steps, "_n_ctx", lambda: 131_072)
    (tmp_path / "game" / "main.js").write_text("const a = 1;\n", encoding="utf-8")
    tools["read_file"](path="main.js")
    cursor = _cursor()
    build_steps.step({}, tmp_path, tools, cursor, {})
    cursor.history = _file_history()
    build_steps.compact(tmp_path, cursor, keep_chars=1200)
    note = cursor.history[1]["content"]
    assert "already read these files and they have NOT changed" in note
    assert "main.js" in note


def test_dedup_alone_can_be_enough_and_the_older_bodies_stay(tmp_path, tools, monkeypatch):
    """A superseded body costs the model nothing to lose — the newest copy is still there. A
    trimmed round costs it the memory of its own work, so when dedup recovers a third of the
    transcript the rounds keep their bodies."""
    monkeypatch.setattr(build_steps, "_n_ctx", lambda: 131_072)
    cursor = _cursor()
    body = "z" * 9000
    cursor.history = [{"role": "user", "content": "make a game"}]
    for i in range(4):                       # the same file written four times over: three go
        cursor.history.append({"role": "assistant", "content": f"step {i}",
                               "tool_calls": [_program(
                                   f'write_file(path="a.js", content={body!r})', i)]})
        cursor.history.append({"role": "tool", "tool_call_id": f"c{i}", "content": "ok"})
    keep = sum(len(json.dumps(m)) for m in cursor.history) // 2
    assert build_steps.compact(tmp_path, cursor, keep_chars=keep) > 0
    kept = json.dumps(cursor.history)
    assert kept.count(body) == 1             # only the newest copy of the file survives
    assert "step 0" in kept                  # ...and the round that wrote it is still readable


def test_a_transcript_over_the_window_never_asks_for_a_negative_reply(tmp_path, tools, monkeypatch):
    """Measured 2026-09-10 against DeepSeek Flash: a prompt the trim could not get under the window
    sent `max_tokens: -408`, which the server rejects as a malformed BODY — and a malformed body
    reads as "your reply was unparseable", which is false and makes the next prompt bigger."""
    monkeypatch.setattr(build_steps, "_n_ctx", lambda: 131_072)
    monkeypatch.setattr(build_steps, "compact", lambda *a, **k: False)   # nothing left to trim
    cursor = _cursor()
    cursor.prompt_tokens = 200_000                                       # the window, overrun
    build_steps.step({}, tmp_path, tools, cursor, {})
    assert cursor.out_cap >= build_steps._MIN_OUT


def test_a_full_window_retry_is_positive_too(tmp_path, tools, monkeypatch):
    monkeypatch.setattr(build_steps, "_n_ctx", lambda: 131_072)
    monkeypatch.setattr(build_steps, "compact", lambda *a, **k: False)
    cursor = _cursor()
    build_steps.step({}, tmp_path, tools, cursor, {})
    cursor.prompt_tokens = 200_000
    build_steps.step({}, tmp_path, tools, cursor,
                     _reply(content="const x = ", usage={"completion_tokens": cursor.out_cap}))
    assert cursor.out_cap >= build_steps._MIN_OUT


def test_staging_never_leaves_the_played_game_half_deleted(tmp_path, monkeypatch):
    """Measured 2026-09-10: a render landing while `stage_for_play` was deleting the live copy
    failed it with `Directory not empty: 'assets'` — after it had already removed 31 of the game's
    55 assets, from the copy someone was playing."""
    from maestro.codegen import staging
    monkeypatch.setattr(staging, "RUNTIME_DIR", tmp_path / "runtime")
    gd = tmp_path / "game"
    (gd / "assets").mkdir(parents=True)
    (gd / "index.html").write_text("v1")
    for i in range(5):
        (gd / "assets" / f"a{i}.png").write_text("art")
    staging.stage_for_play(tmp_path, "abc123")
    staged = tmp_path / "runtime" / "games" / "abc123"

    real_copytree = staging.shutil.copytree

    def land_a_render_mid_copy(src, dst, *a, **kw):
        out = real_copytree(src, dst, *a, **kw)
        (staged / "assets" / "late.png").write_text("landed while we copied")
        return out

    monkeypatch.setattr(staging.shutil, "copytree", land_a_render_mid_copy)
    (gd / "index.html").write_text("v2")
    staging.stage_for_play(tmp_path, "abc123")

    assert (staged / "index.html").read_text() == "v2"
    assert sorted(p.name for p in (staged / "assets").iterdir()) == \
        [f"a{i}.png" for i in range(5)], "the new copy is whole, not a partial delete"


def test_staging_swaps_in_and_leaves_nothing_behind(tmp_path, monkeypatch):
    from maestro.codegen import staging
    monkeypatch.setattr(staging, "RUNTIME_DIR", tmp_path / "runtime")
    gd = tmp_path / "game"
    gd.mkdir()
    (gd / "index.html").write_text("one")
    staging.stage_for_play(tmp_path, "abc123")
    (gd / "index.html").write_text("two")
    staging.stage_for_play(tmp_path, "abc123")
    games = tmp_path / "runtime" / "games"
    assert (games / "abc123" / "index.html").read_text() == "two"
    assert [p.name for p in games.iterdir()] == ["abc123"], "no staging or stale folder is left"


def test_a_crashed_staging_does_not_block_the_next_one(tmp_path, monkeypatch):
    """A leftover working folder from a killed process is owed nothing."""
    from maestro.codegen import staging
    monkeypatch.setattr(staging, "RUNTIME_DIR", tmp_path / "runtime")
    games = tmp_path / "runtime" / "games"
    (games / "_abc123.staging" / "junk").mkdir(parents=True)
    (games / "_abc123.stale").mkdir(parents=True)
    gd = tmp_path / "game"
    gd.mkdir()
    (gd / "index.html").write_text("fresh")
    staging.stage_for_play(tmp_path, "abc123")
    assert (games / "abc123" / "index.html").read_text() == "fresh"
    assert [p.name for p in games.iterdir()] == ["abc123"]
