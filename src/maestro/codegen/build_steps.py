"""The build's turn machine, as a resumable step.

`step(spec, run_dir, tools, cursor, result) -> Infer | Done`. With no LLM result to apply yet it
builds and returns the FIRST inference request; otherwise it applies the completed turn's tool calls
and either returns the NEXT request or `Done`. The driver (build_chain) enqueues each `Infer` as one
`llm` job, dies, and re-enters this function on the completion — so the whole loop is spread across
process deaths, its scratch carried in the durable cursor.

The transcript IS the memory: compaction (the newest copy of each file's body wins, then bodies
out of the oldest rounds until the tail fits, then the oldest whole rounds dropped and the model
re-grounded on the file listing) is what keeps it inside the context window.
"""

from __future__ import annotations

import ast
import hashlib
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Union

from llm_clients.message_builder import MessageBuilder
from maestro.codegen import asset_use, code_map, file_state, turn_log
from maestro.codegen.pyexec import runner
from maestro.codegen.staging import game_dir
from maestro.services import parse_args, parse_args_checked
from maestro.tool_calls import parse_tool_calls

logger = logging.getLogger(__name__)

_PROMPTS = Path(__file__).resolve().parent / "prompts"

MAX_TURNS = 200
# A turn the driver was asked to re-send with nothing to apply (a reaper re-drive). Each one
# enqueues a fresh job, so a worker that dies every time would re-drive forever.
_REDRIVE_GIVE_UP = 8
# Compact when the last prompt left less than this many tokens of the window for the reply; keep
# this fraction of the window afterwards.
_COMPACT_ROOM = 16_000
_COMPACT_KEEP = 0.33
# Recovered by dedup alone, as a fraction of the transcript, that makes trimming unnecessary.
_DEDUP_ENOUGH = 0.30
# A server refusal of the PROMPT rather than the reply. Nothing the model wrote is at fault, so
# the answer is to trim the transcript and send it again.
_PROMPT_REFUSED = ("context_length_exceeded", "max_context", "max_tokens must be positive")
# Consecutive turns with no tool call before the build gives up. Each nudge differs: repeating one
# verbatim reproduces the reply that earned it.
_NO_CALL_GIVE_UP = 4
_OUT_CAP = 30_000
# A reply needs room to exist. When the estimate says the transcript has eaten the window, the
# answer is to trim again, never to ask for a non-positive number of tokens: engines report that
# as a malformed BODY, which reads as "your reply was unparseable" and makes the next prompt
# bigger (2026-09-10, DeepSeek Flash: `max_tokens: invalid value: integer -408`).
_MIN_OUT = 2_000
_NUDGES = [
    "Keep going. Call the python tool with a program, or call done() in one if the game is "
    "finished.",
    "That reply carried no python tool call, so nothing ran. Emit an actual tool call whose "
    "`code` is the program.",
    "Still no program landed. Call the python tool with exactly this and nothing else: "
    "write_file(path=\"index.html\", content=\"<!doctype html><title>game</title>\"), then "
    "build the rest.",
]
# The third nudge on a game that already has its page (a fix, a later stage) would overwrite it.
_NUDGE_EXISTING = ("Still no program landed. Call the python tool with a program that calls "
                   "list_files(), then reads the file you mean to change and edits it.")


def _nudge(streak: int, run_dir) -> str:
    if streak >= len(_NUDGES) and (game_dir(run_dir) / "index.html").exists():
        return _NUDGE_EXISTING
    return _NUDGES[min(streak, len(_NUDGES)) - 1]


# What a reply too big to land is answered with, wherever it surfaces — as a cut-off argument, as a
# cut-off reply, or as the server refusing to parse its own model's output. The remedy is the same
# one every time, so the model reads the same sentence rather than three descriptions of one event.
_TOO_BIG = ("Write the file in smaller pieces: split the game across several files, or write one "
            "section at a time. Do not repeat a whole large file to change a small part of it.")


# Satisfiable by naming nothing, so a finished game passes it on the next turn. A bar the model
# cannot clear is answered by contorting the game until the step cap.
_DONE_NUDGE = (
    "Not finished yet. Call list_files, then name anything a player meets in the first thirty "
    "seconds that is missing or unfinished — how they learn the controls, what the first screen "
    "shows, whether every button does something. Every name your code uses must be declared "
    "somewhere — a variable referenced but never defined crashes the game on load. Build what "
    "you find, then call done again. If nothing is missing, call done again."
)


def _done_nudge(run_dir) -> str:
    """The nudge, plus whatever the art audit found. It rides the nudge rather than a turn of its
    own because the nudge is already the one place the build asks what is unfinished — and it is
    asked ONCE, so a model told twice does not start inventing work."""
    art = asset_use.report(asset_use.audit(run_dir))
    return f"{_DONE_NUDGE} {art}" if art else _DONE_NUDGE


@dataclass
class Infer:
    """Suspend here: enqueue one `llm` job with these messages, die, resume on completion."""
    messages: List[dict]
    schemas: List[dict]
    max_tokens: int
    report: Optional[str] = None      # progress line emitted when this turn is enqueued


@dataclass
class Done:
    report: str


Outcome = Union[Infer, Done]


# One tool: the model writes a PROGRAM, and the program calls the build's functions. Seven schemas
# cost 1,831 tokens of every turn's window against this one's 266 (measured 2026-09-07), and a
# program can loop, branch and check what it just did — twenty-two art asks from a roster, a
# verifier over a table it generated, a search across a file that never enters the transcript.
# The functions themselves are documented in build.txt, where the model reads them once per turn
# rather than in a schema it re-reads with every tool.
PYTHON_SCHEMA = {"type": "function", "function": {
    "name": "python",
    "description": ("Run one Python program. It may call list_files, read_file, write_file, "
                    "edit_file, generate_media, compose_world, check_syntax and done. Whatever "
                    "it prints comes back to you."),
    "parameters": {"type": "object",
                   "properties": {"code": {"type": "string"}},
                   "required": ["code"]}}}

SCHEMAS = [PYTHON_SCHEMA]


def _n_ctx() -> int:
    from config.settings_manager import settings_manager
    return int((settings_manager.get_settings().get("llm") or {}).get("n_ctx") or 32768)


def step(spec, run_dir, tools, cursor, result, error: Optional[str] = None) -> Outcome:
    """One turn: apply the completed turn's tool calls, then ask for the next. `None` is no turn to
    apply (re-ask as-is); `{}` is a turn that ran and returned nothing, which the no-call branch
    answers. `error` is a turn the WORKER could not deliver, which is neither."""
    if not cursor.started:
        cursor.started = True
        cursor.system = (_PROMPTS / "build.txt").read_text(encoding="utf-8")
        cursor.history = [{"role": "user", "content": cursor.request or _request_from(spec)}]
        return _infer(run_dir, cursor)

    if error:
        if any(m in error for m in _PROMPT_REFUSED):
            # The server refused the PROMPT for its size. Telling the model its reply was unreadable
            # would be false AND would make the next prompt bigger — measured 2026-09-08, run
            # 90a89ba593ee: one round jumped the window, the estimate never crossed the compaction
            # trigger, and the build re-sent a prompt that grew 124 tokens an attempt until it died.
            cursor.turn += 1
            before = cursor.compacted
            cursor.prompt_tokens = _n_ctx()      # forces the trim the estimate missed
            nxt = _infer(run_dir, cursor, report="the prompt outgrew the window; trimmed it")
            if cursor.compacted == before:
                return Done("stalled: the prompt is over the window and nothing is left to trim")
            return nxt
        # The reply never reached us: an oversized tool call comes back as a 500 from the inference
        # server's OWN argument parser, and the model that wrote it hears nothing. Told only that it
        # said nothing, it resends the same oversized call and the build dies four turns later
        # (measured 2026-08-01: build 2ac37ea38256, one 64 KB write_file, four identical 500s).
        cursor.turn += 1
        cursor.no_call_streak += 1
        cursor.history.append({"role": "user", "content":
                               "The inference server could not read your last reply, so nothing "
                               f"was saved. It reported: {_clip(error, 200)}\n\nA reply too large "
                               f"to parse is the usual cause. {_TOO_BIG}"})
        if cursor.no_call_streak >= _NO_CALL_GIVE_UP:
            return Done(f"stalled: {cursor.no_call_streak} turns the server could not read")
        return _infer(run_dir, cursor, report=f"turn rejected by the server: {_clip(error, 80)}")

    if result is None:
        cursor.redriven += 1
        if cursor.redriven >= _REDRIVE_GIVE_UP:
            return Done(f"stalled: {cursor.redriven} turns never ran")
        return _infer(run_dir, cursor, report="re-sent the turn that never ran")

    usage = result.get("usage") or {}
    cursor.prompt_tokens = usage.get("prompt_tokens") or cursor.prompt_tokens
    if usage.get("prompt_tokens"):
        sent = len(cursor.system) + sum(len(json.dumps(m)) for m in cursor.history)
        cursor.chars_per_token = sent / usage["prompt_tokens"]
    message = (result.get("choices") or [{}])[0].get("message", {}) or {}
    content = message.get("content") or ""
    calls = [tc for tc in (message.get("tool_calls") or []) if tc.get("function", {}).get("name")]
    if not calls:
        # The server's parser didn't claim the model's tool-call syntax — recover it from the text.
        calls = parse_tool_calls(content, SCHEMAS)
    cursor.turn += 1

    if not calls and (usage.get("completion_tokens") or 0) >= cursor.out_cap - 32:
        cursor.no_call_streak += 1
        if cursor.no_call_streak >= _NO_CALL_GIVE_UP:
            return Done(f"stalled: {cursor.no_call_streak} turns cut off by the output limit")
        return _infer(run_dir, cursor, report="the reply ran out of room — sending the turn again",
                      full_window=True)

    reply = {"role": "assistant", "content": content}
    if message.get("reasoning_content"):
        # The server's cached sequence ends in this thinking; a history that sends it back extends
        # that sequence exactly, so the next turn prefills its own tail instead of the whole window.
        reply["reasoning_content"] = message["reasoning_content"]

    if not calls:
        # The same nudge produces the same reply, so each one differs and the streak gives up.
        cursor.no_call_streak += 1
        cursor.history.append(reply)
        cursor.history.append({"role": "user", "content":
                               _nudge(cursor.no_call_streak, run_dir)})
        if cursor.no_call_streak >= _NO_CALL_GIVE_UP:
            return Done(f"stalled: {cursor.no_call_streak} turns with no tool call")
    else:
        if len(content) > 2000:
            reply["content"] = "[…analysis truncated…]\n" + content[-2000:]
        cursor.no_call_streak = 0
        # One program is the whole of a turn. A model that emits two calls means the second to run
        # after the first, and running both without showing it the first's output would be acting
        # on a result it never saw — so the rest are answered, unrun, and it decides.
        cursor.history.append({**reply, "tool_calls": calls[:1]})
        _apply(tools, cursor, calls[0], run_dir)
        if len(calls) > 1:
            cursor.history.append({"role": "user", "content":
                                   f"Only the first program ran; the other {len(calls) - 1} did "
                                   "not. Send one program per reply — what the first one printed "
                                   "is above, so write the next one knowing it."})
        if cursor.finished:
            return Done(f"done after {cursor.turn} turn(s): {cursor.summary}")

    if cursor.turn >= MAX_TURNS:
        return Done(f"hit the {MAX_TURNS}-turn cap")
    return _infer(run_dir, cursor)


def _clip(text: str, n: int) -> str:
    """Cut to `n` chars on a word boundary. A feed line that ends mid-word reads as a bug."""
    text = " ".join(str(text).split())
    if len(text) <= n:
        return text
    return text[:n].rsplit(" ", 1)[0] + "…"


def _opening_line(cursor) -> str:
    """The opening turn's feed line carries the text that was sent — the whole build follows from
    it, and the feed is where a person checks that what they typed is what went."""
    sent = _clip(cursor.history[0]["content"] if cursor.history else "", 300)
    label = {"fix": "sent the fix note", "change": "sent the change note"}.get(cursor.kind, "sent the prompt")
    return f"{label}: {sent}" if sent else label


def _actions_of(outcome) -> List[str]:
    """One turn's program as lines for the build feed — what the model DID, since the turn counter
    alone says only that it is still going. A failure carries its reason: the whole point of
    watching the feed is seeing the build go wrong before its step cap says so.

    Repeats are folded ("wrote 6 files") because a program may make a hundred calls and the feed is
    read by a person."""
    if outcome.refused:
        return [f"program refused: {_clip(outcome.refused, 110)}"]
    verb = {"write_file": "wrote", "edit_file": "edited", "read_file": "read",
            "generate_media": "asked for art", "compose_world": "built a world",
            "list_files": "listed files", "check_syntax": "checked the syntax", "done": "done"}
    lines, seen = [], {}
    for name, target, ok in outcome.ledger:
        if name == "done":
            lines.append("said it is done")
            continue
        key = (verb.get(name, name), ok)
        seen.setdefault(key, [])
        if target and target not in seen[key]:
            seen[key].append(target)
        elif not target:
            seen[key].append("")
    for (word, ok), targets in seen.items():
        named = [x for x in targets if x]
        failed = "" if ok else " — failed"
        if len(named) > 3:
            noun = "asks" if word.startswith("asked") else "files"
            lines.append(f"{word} {len(named)} {noun}" + (failed and " — all failed"))
        elif named:
            lines.append(f"{word} {', '.join(named)}{failed}")
        else:
            lines.append(f"{word}{failed}")
    if outcome.timed_out:
        lines.append("the program ran out of time and was stopped")
    elif outcome.failed:
        lines.append("the program raised")
    return lines or ["ran a program that called nothing"]


def _repeat_note(cursor, code: str, outcome) -> Optional[str]:
    """The error text alone cannot say it has been seen before, so a resent program repeats to the
    step cap. Keyed on the program, since the program is now the whole of what a turn sends
    (measured 2026-07-29 on single calls: 12 identical failing edits, every one scored as the
    first)."""
    if not (outcome.refused or outcome.failed or any(not ok for _, _, ok in outcome.ledger)):
        cursor.repeat_counts.pop(_sig(code), None)
        return None
    sig = _sig(code)
    n = cursor.repeat_counts.get(sig, 0) + 1
    cursor.repeat_counts[sig] = n
    if n < 2:
        return None
    return (f"\n\nYou have now sent this exact program {n} times and it has gone wrong every "
            "time. Try something new.")


def _sig(code: str) -> str:
    return hashlib.sha1(code.encode("utf-8")).hexdigest()


def _result_of(outcome) -> str:
    """What the turn hears back: what the program PRINTED, then the calls it made.

    The ledger rides every result because a failed call now returns its error as a value rather
    than raising — a program that does not look at what a call gave back would otherwise never
    learn it failed, and would carry on believing ten files were written when nine were."""
    if outcome.refused:
        return outcome.refused
    body = outcome.stdout.strip() or "(the program ran and printed nothing)"
    if not outcome.ledger:
        return body
    lines = [f"  {name} {target}".rstrip() + ("" if ok else "  — FAILED")
             for name, target, ok in outcome.ledger]
    failed = sum(1 for _, _, ok in outcome.ledger if not ok)
    if len(lines) > 40:
        lines = lines[:20] + [f"  … {len(lines) - 40} more …"] + lines[-20:]
    tail = "\n\n[what the program called:\n" + "\n".join(lines) + "\n]"
    if failed:
        tail += (f"\n[{failed} of those FAILED. A failed call returns its error as a string "
                 "instead of doing anything; everything after it still ran.]")
    return body + tail


def _cut_off(tc) -> bool:
    """Did this call's argument JSON stop mid-write? The model reached the output cap while
    streaming a big program, so the arguments are unterminated and nothing can be recovered from
    them. parse_args answers {} — which reaches the tool as a MISSING argument, and a model told it
    forgot `code` resends the same oversized program (measured 2026-08-01 on write_file: a 64 KB
    call reported as KeyError: 'path', then four turns the server itself refused, then a dead
    build)."""
    return not parse_args_checked(tc["function"].get("arguments"))[1]


def _apply(tools, cursor, tc, run_dir) -> bool:
    """Run one program and record what it did. Answers whether the build is finished."""
    if _cut_off(tc):
        cursor.actions.append("the program was cut off by the output limit")
        cursor.history.append({"role": "tool", "tool_call_id": tc.get("id"),
                               "content": "Your reply hit the output token limit part-way through "
                                          f"the program, so nothing ran. {_TOO_BIG}"})
        return False
    code = parse_args(tc["function"].get("arguments")).get("code")
    if not isinstance(code, str) or not code.strip():
        cursor.actions.append("no program in the call")
        cursor.history.append({"role": "tool", "tool_call_id": tc.get("id"),
                               "content": "That call carried no `code`, so nothing ran. Send the "
                                          "program as the `code` argument."})
        return False

    outcome = runner.run(code, tools)
    cursor.actions.extend(_actions_of(outcome))
    content = _result_of(outcome) + (_repeat_note(cursor, code, outcome) or "")

    finished = [t for name, t, ok in outcome.ledger if name == "done" and ok]
    if finished and not cursor.done_nudged:
        # The nudge is the TOOL RESULT, not a user message after it: one message answers one call,
        # and no round is left with its `tool` half missing.
        cursor.done_nudged = True
        cursor.history.append({"role": "tool", "tool_call_id": tc.get("id"),
                               "content": content + "\n\n" + _done_nudge(run_dir)})
        return False
    cursor.history.append({"role": "tool", "tool_call_id": tc.get("id"), "content": content})
    if finished:
        cursor.finished = True
        cursor.summary = _clip(finished[0], 400)
    return cursor.finished


def _estimate(cursor) -> int:
    """Tokens the next prompt will cost. The last prompt the server COUNTED plus the rounds added
    since, because re-estimating a whole near-full transcript at 3 chars a token overshoots; the
    whole thing only until the server has counted one."""
    chars = lambda ms: sum(len(json.dumps(m)) for m in ms)
    if cursor.prompt_tokens:
        return cursor.prompt_tokens + chars(cursor.history[cursor.logged:]) // 3
    return (len(cursor.system) + chars(cursor.history)) // 3


def _compact(run_dir, cursor, ctx: int) -> None:
    # A build's transcript runs nearer 3 chars a token than 4; a guessed 4 kept ~60K of a 131K
    # window instead of a third.
    if compact(run_dir, cursor, int(ctx * _COMPACT_KEEP * (cursor.chars_per_token or 3))):
        cursor.compacted += 1
        cursor.prompt_tokens = 0   # unknown until the server reports the trimmed prompt back


def _infer(run_dir, cursor, report: Optional[str] = None, *, full_window: bool = False) -> Infer:
    ctx = _n_ctx()
    if _estimate(cursor) > ctx - _COMPACT_ROOM:
        _compact(run_dir, cursor, ctx)
    if ctx - _estimate(cursor) < _MIN_OUT:
        # The trim above ran on an estimate and did not get under the window — the same miss the
        # server's own refusal forces a second pass for.
        cursor.prompt_tokens = ctx
        _compact(run_dir, cursor, ctx)
    msgs = MessageBuilder(cursor.system).extend(cursor.history).build()
    # No actions means the turn called no tool: either the opening turn (the prompt has just been
    # sent and nothing has happened yet) or one the nudge is answering.
    report = report or ", ".join(cursor.actions) or (
        _opening_line(cursor) if cursor.turn == 0 else "no tool call — asked again")
    if cursor.compacted:
        report += f" (compacted {cursor.compacted}×)"
    cursor.actions = []
    # Overrunning is how a turn asks for the rest of the window — not the streak, which a turn that
    # merely called no tool shares.
    room = max(_MIN_OUT, ctx - _estimate(cursor))
    cursor.out_cap = room if full_window else min(_OUT_CAP, room)
    return Infer(msgs, SCHEMAS, cursor.out_cap, report=report)


def rounds(history: List[dict]) -> List[List[dict]]:
    """Split the transcript into whole rounds — one assistant message plus the tool results
    answering it. Dropping must never split one: a `tool` message whose matching assistant
    `tool_calls` is gone is an orphan, and a chat template is entitled to refuse it."""
    out, cur = [], []
    for m in history[1:]:            # history[0] is the request, never dropped
        if m["role"] != "tool" and cur:
            out.append(cur)
            cur = []
        cur.append(m)
    if cur:
        out.append(cur)
    return out


# Why a stub states where the bytes are and never says "read it again": told to re-read, the model
# re-reads the whole project after every compaction, and the reads refill the window (measured
# 2026-09-06: two prod builds spent ~150 of 200 steps that way and shipped nothing).
_SUPERSEDED = "a newer copy is later in this transcript"
_ON_DISK = "on disk; not repeated here"

# A body is worth stubbing at this size and no smaller: below it the stub costs about what the
# text does.
_BODY = 200


def _program_of(tc: dict) -> Optional[str]:
    if (tc.get("function") or {}).get("name") != "python":
        return None
    code = parse_args(tc["function"].get("arguments")).get("code")
    return code if isinstance(code, str) else None


def _written_in(code: str) -> List[tuple]:
    """Every literal file body the program writes, as (path, span, chars) — one per literal.

    `write_file` takes either argument by position or by name, and a body is a string literal, a
    name bound once to one, or those added together: a model writes a big file in named sections
    and passes them in, so the literal usually sits in an assignment, not in the call. The span is
    where the literal SITS in the source, so it can be cut out and the rest of the program left
    exactly as the model wrote it. A body built at runtime — formatted, joined, read back and
    replaced, a name assigned twice — has no span and is left alone: the program is the only record
    of how it was made."""
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return []
    stores: Dict[str, int] = {}
    for n in ast.walk(tree):
        if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store):
            stores[n.id] = stores.get(n.id, 0) + 1
    bound = {t.id: n.value for n in ast.walk(tree) if isinstance(n, ast.Assign)
             for t in n.targets if isinstance(t, ast.Name)}

    def literals(node, through=frozenset()) -> Optional[list]:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return [node]
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            left, right = literals(node.left, through), literals(node.right, through)
            return None if left is None or right is None else left + right
        if (isinstance(node, ast.Name) and stores.get(node.id) == 1 and node.id in bound
                and node.id not in through):   # `x = x + "..."` is bound once and never resolves
            return literals(bound[node.id], through | {node.id})
        return None

    out = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "write_file"):
            continue
        args = {kw.arg: kw.value for kw in node.keywords}
        path = node.args[0] if node.args else args.get("path")
        body = node.args[1] if len(node.args) > 1 else args.get("content")
        if not (isinstance(path, ast.Constant) and isinstance(path.value, str)) or body is None:
            continue
        for lit in literals(body) or []:
            if len(lit.value) >= _BODY:
                out.append((path.value, (lit.lineno, lit.col_offset,
                                         lit.end_lineno, lit.end_col_offset), len(lit.value)))
    return out


def _splice(code: str, spans: List[tuple]) -> str:
    """The program with each named span replaced by a one-line stub. Cutting from the END keeps
    every earlier span's offsets true."""
    lines = code.split("\n")
    for (l1, c1, l2, c2), text in sorted(spans, key=lambda s: s[0], reverse=True):
        head = lines[l1 - 1][:c1]
        tail = lines[l2 - 1][c2:]
        lines[l1 - 1:l2] = [head + text + tail]
    return "\n".join(lines)


def _stub_call(tc: dict, why: str) -> Optional[dict]:
    """The program with its written-out file bodies replaced by a note saying where they are.

    The round keeps its shape — the loops, the art asks, the order it did things in — and loses
    only bytes that are also on disk. The transcript is never re-run, so a program that no longer
    executes is not a problem; what it has to keep saying is what the model DID."""
    code = _program_of(tc)
    if not code:
        return None
    # One bound literal can feed two writes; it is cut once.
    spans = list({span: (span, json.dumps(f"[the {n} chars written to {path} — {why}]"))
                  for path, span, n in reversed(_written_in(code))}.values())
    if not spans:
        return None
    args = parse_args(tc["function"].get("arguments"))
    args["code"] = _splice(code, spans)
    return {**tc, "function": {**tc["function"],
                               "arguments": json.dumps(args, ensure_ascii=False)}}


def _write_keys(tc: dict) -> List[tuple]:
    return [("write", path) for path, _, _ in _written_in(_program_of(tc) or "")]


# What a program can call without changing anything. A round of only these is a list of filenames
# the model once opened.
_LOOKING = {"read_file", "list_files", "check_syntax"}


def _called_in(code: str) -> set:
    """The tool functions the program calls, by name."""
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return set()
    return {n.func.id for n in ast.walk(tree)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}


def _read_only(group: List[dict]) -> bool:
    """A round that only LOOKED. Judged by what the program called, never by whether it wrote
    bytes worth keeping: an edit writes no body and a one-line file writes a short one, and
    dropping either would lose the record of a change the model made.

    Once the map in the compaction note says what each file is, a round that only looked is a list
    of filenames, and keeping it teaches the model that opening files is the work."""
    calls = group[0].get("tool_calls") or []
    if not calls or any(_program_of(tc) is None for tc in calls):
        return False
    named = set().union(*(_called_in(_program_of(tc)) for tc in calls))
    return bool(named & _LOOKING) and not (named - _LOOKING - {"print", "len", "range",
                                                               "sorted", "enumerate", "str",
                                                               "int", "repr", "set", "list"})


def drop_read_only_rounds(history: List[dict]) -> List[dict]:
    groups = rounds(history)
    return history[:1] + [m for g in groups if not _read_only(g) for m in g]


def trim_bodies(history: List[dict], rounds_to_trim: int) -> List[dict]:
    """The oldest `rounds_to_trim` rounds with their file bodies replaced by a stub naming the path
    and size. The round keeps its shape — what was written, read and edited, in order — and loses
    only bytes that are also on disk."""
    groups = rounds(history)
    out = list(history[:1])
    for i, g in enumerate(groups):
        for m in g:
            if i < rounds_to_trim and m.get("tool_calls"):
                m = {**m, "tool_calls": [_stub_call(tc, _ON_DISK) or tc for tc in m["tool_calls"]]}
            out.append(m)
    return out


def strip_reasoning(history: List[dict]) -> List[dict]:
    """Every turn's thinking gone. A cut already breaks the server's cached prefix, so this is the
    one moment losing it costs no re-prefill — and kept past it, thinking would fill the window."""
    return [{k: v for k, v in m.items() if k != "reasoning_content"} for m in history]


def dedupe_bodies(history: List[dict]) -> List[dict]:
    """The newest written body of each file wins: every older write of the same path becomes a
    stub pointing at the newer copy.

    Only writes are deduped. A read no longer carries a body at all — the program reads a file
    into a variable and prints what it chose to, so the transcript holds the model's own summary,
    which is not something to stand in for. Edits stay whole: an edit after the live write is what
    the model changed since."""
    seen: set = set()
    out = []
    for m in reversed(history[1:]):
        if m.get("tool_calls"):
            calls = []
            for tc in m["tool_calls"]:
                keys = _write_keys(tc)
                if keys and all(k in seen for k in keys):
                    tc = _stub_call(tc, _SUPERSEDED) or tc
                seen.update(keys)
                calls.append(tc)
            m = {**m, "tool_calls": calls}
        out.append(m)
    return history[:1] + out[::-1]


def compact(run_dir, cursor, keep_chars: int) -> int:
    """Drop every turn's thinking, stub every superseded file body and drop the rounds that only
    looked at files; then, oldest round first, stub the bodies out of rounds until the tail fits
    `keep_chars`; only if it still does not fit, drop the OLDEST whole rounds. Every compaction ends with a note
    carrying the CODE MAP — each file, its imports, every declaration with its line range — so
    the model regains the whole picture without a read, and reads by range when it needs one.

    A transcript is mostly file bodies that are also on disk, and dropping a round that wrote a
    file loses the model's memory of having written it. Trimmed, the round still says what the
    model did; the bytes come back on `read_file`. The newest rounds are the last to lose their
    bodies, because they are what the model is about to edit against. Returns the rounds trimmed
    or dropped, 1 when only thinking, superseded bodies or read-only rounds went, 0 when nothing
    changed."""
    size = lambda msgs: sum(len(json.dumps(m)) for m in msgs)
    was = size(cursor.history)
    history = drop_read_only_rounds(dedupe_bodies(strip_reasoning(cursor.history)))
    deduped = history != cursor.history
    # Superseded bodies and read-only rounds cost the model NOTHING to lose — the newest copy of
    # every file is still there and the rounds that went looked at files without changing any. When
    # that alone recovers a third of the transcript, the older rounds keep their bodies: a round
    # trimmed is a round the model can no longer read its own work out of. (Cline orders its
    # per-file dedup ahead of truncation on the same rule, at 30%.)
    if deduped and size(history) <= was * (1 - _DEDUP_ENOUGH):
        keep_chars = max(keep_chars, size(history))
    groups = rounds(history)
    whole = [size(g) for g in groups]
    stubbed = [size(g) for g in rounds(trim_bodies(history, len(groups)))]
    total, trimmed = sum(whole), 0
    while total > keep_chars and trimmed < len(groups):
        total += stubbed[trimmed] - whole[trimmed]
        trimmed += 1
    if whole[:trimmed] == stubbed[:trimmed]:
        trimmed = 0
    history = trim_bodies(history, trimmed) if trimmed else history
    groups = rounds(history)
    dropped, removed = 0, 0
    while groups and total > keep_chars:
        g = groups.pop(0)
        total -= size(g)
        dropped += 1
        removed += len(g)
    if not deduped and not trimmed and not dropped:
        return 0
    kept = [m for g in groups for m in g]
    root = game_dir(run_dir)
    listing = "\n".join(
        f"{p.relative_to(root)} ({p.stat().st_size} bytes)"
        for p in sorted(root.rglob("*"))
        if p.is_file() and not p.name.startswith("_") and str(p.relative_to(root)).startswith("assets/"))
    state = file_state.render(run_dir, root)
    note = {"role": "user", "content":
            "[Earlier steps were trimmed to save room. Every file is on disk exactly as you last "
            "wrote or read it. Below is the whole project: each file, what it imports, and every "
            "declaration with its line range. Read a file only to edit it, and read the lines "
            "you need with offset and lines rather than the whole file.]\n\n"
            + (code_map.render(root) or "(no source files yet)")
            + (f"\n\n{state}" if state else "")
            + (f"\n\nArt on disk:\n{listing}" if listing else "")}
    cursor.history = history[:1] + [note] + kept
    # The changed messages are already in the turn log; the record replays the same trim and drop
    # there too, so the archive shows what was really sent rather than the transcript never re-sent.
    turn_log.append_compact(run_dir, turn=cursor.turn, trimmed=trimmed, dropped=dropped,
                            note=note["content"])
    cursor.logged = max(1, cursor.logged - removed + 1)
    return trimmed + dropped or 1


def _request_from(spec) -> str:
    """The build's one user message: the run's prompt, byte for byte as stored."""
    return str((spec or {}).get("request") or "").strip() or "Make a small, playable browser game."
