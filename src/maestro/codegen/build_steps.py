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

import hashlib
import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Union

from llm_clients.message_builder import MessageBuilder
from maestro.codegen import asset_use, turn_log
from maestro.codegen.staging import game_dir
from maestro.services import parse_args, parse_args_checked
from maestro.tool_calls import parse_tool_calls

logger = logging.getLogger(__name__)

_PROMPTS = Path(__file__).resolve().parent / "prompts"

MAX_TURNS = 200
# Ties to tools.MAX_READ_CHARS — a read cut here too would contradict its own truncation note.
_MAX_TOOL_CHARS = 20_000
# A turn the driver was asked to re-send with nothing to apply (a reaper re-drive). Each one
# enqueues a fresh job, so a worker that dies every time would re-drive forever.
_REDRIVE_GIVE_UP = 8
# Compact when the last prompt left less than this many tokens of the window for the reply; keep
# this fraction of the window afterwards.
_COMPACT_ROOM = 16_000
_COMPACT_KEEP = 0.33
# Consecutive turns with no tool call before the build gives up. Each nudge differs: repeating one
# verbatim reproduces the reply that earned it.
_NO_CALL_GIVE_UP = 4
_NUDGES = [
    "Keep going. Use a tool, or call done if the game is finished.",
    "That reply contained no tool call, so nothing was saved. Emit an actual tool call.",
    "Still no tool call landed. Call write_file with path \"index.html\" and a minimal page as "
    "content, then build the rest.",
]
# The third nudge on a game that already has its page (a fix, a later stage) would overwrite it.
_NUDGE_EXISTING = ("Still no tool call landed. Call list_files, then read_file the file you mean "
                   "to change and edit_file it.")


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


# Descriptions stay short: every clause is another instruction competing with the request, on every
# turn.
LIST_SCHEMA = {"type": "function", "function": {
    "name": "list_files",
    "description": "List the files in the project directory.",
    "parameters": {"type": "object", "properties": {}, "required": []}}}
READ_SCHEMA = {"type": "function", "function": {
    "name": "read_file",
    "description": "Read a file from the project directory.",
    "parameters": {"type": "object",
                   "properties": {"path": {"type": "string"},
                                  "offset": {"type": "integer", "description":
                                             "First line to show, 1-based. Use it to read past a "
                                             "read that said the file was too long."}},
                   "required": ["path"]}}}
WRITE_SCHEMA = {"type": "function", "function": {
    "name": "write_file",
    "description": "Write a file to the project directory, replacing it if it exists.",
    "parameters": {"type": "object",
                   "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
                   "required": ["path", "content"]}}}
EDIT_SCHEMA = {"type": "function", "function": {
    "name": "edit_file",
    "description": ("Change part of an existing file. Replaces the exact text in old_text with "
                    "new_text. old_text must appear exactly once in the file. Use this instead of "
                    "rewriting a whole file to change a small part of it."),
    "parameters": {"type": "object",
                   "properties": {"path": {"type": "string"},
                                  "old_text": {"type": "string", "description":
                                               "Exact text to replace, with real newlines and "
                                               "quotes — never \\n or \\\" as characters."},
                                  "new_text": {"type": "string", "description": "Text to put in its place."}},
                   "required": ["path", "old_text", "new_text"]}}}
MEDIA_SCHEMA = {"type": "function", "function": {
    "name": "generate_media",
    "description": ("Have an artist draw an image, an animated actor or a 3D model for the game. "
                    "Returns the path for the art. A game's art shares one visual style: before "
                    "the first call, write one style phrase naming medium, palette and outline "
                    "(like \"painted cartoon style, warm forest palette, soft dark outlines\") "
                    "and pass it as `style` on every call. The style is stylized and cartoonish "
                    "— chunky simplified shapes, bold colour — unless the request names another "
                    "style. Example:\n"
                    "{\"id\": \"knight\", \"kind\": \"actor\", "
                    "\"subject\": \"a chibi knight in a silver helmet with a red plume, blue "
                    "tunic, round wooden shield, short sword\", "
                    "\"style\": \"chunky cartoon chibi style, bold flat colors, soft dark "
                    "outlines\", "
                    "\"details\": {\"body\": \"biped\", \"view\": \"side\", "
                    "\"facings\": 4, \"anims\": [{\"name\": \"walk\", \"action\": "
                    "\"walks in place, legs alternating, arms swinging\"}]}}"),
    "parameters": {"type": "object",
                   "properties": {"id": {"type": "string",
                                         "description": "Short name: letters, digits, - and _."},
                                  "kind": {"type": "string",
                                           "enum": ["sprite", "actor", "tile", "scene", "mesh"],
                                           "description":
                                               "sprite: one thing, cut out, drawn on top of the "
                                               "game — a key, a lamp, a coin. actor: a thing with "
                                               "a body — a character, a creature, a vehicle — "
                                               "drawn as a sprite, or animated into a sheet of "
                                               "the anims you name and drawn with lib/sprites.js. "
                                               "tile: a surface the game repeats, fills its "
                                               "frame — describe real ground or wall seen from "
                                               "directly above (\"mossy forest floor with small "
                                               "stones, seen from directly above\"); the words "
                                               "texture, seamless and pattern come back as noise; "
                                               "a tile is drawn small in the game, so build it "
                                               "from large simple shapes with minimal fine detail "
                                               "— fine detail turns to noise at game size — and "
                                               "in muted, low-contrast colors: the ground is the "
                                               "backdrop the sprites must stand out against, so a "
                                               "tile keeps the style's palette but never its "
                                               "vividness or bold outlines. "
                                               "scene: a whole picture the game draws behind "
                                               "everything. mesh: a 3D model."},
                                  "subject": {"type": "string",
                                              "description":
                                                  "WHAT to draw, described for an artist, with no "
                                                  "style words. Lead with the thing, then mood "
                                                  "and lighting, then detail — trailing detail is "
                                                  "what the artist drops. Name colors that stand "
                                                  "out against the game's background."},
                                  "style": {"type": "string",
                                            "description":
                                                "The game's one style phrase: medium, palette, "
                                                "outline. The same words on every call."},
                                  "details": {"type": "object",
                                              "description":
                                                  "Facts about the thing that are not prose. "
                                                  "body: its body plan — biped, quadruped, or "
                                                  "whatever it is (winged, wheeled, a box, a "
                                                  "blob). view: how the game shows it — side, "
                                                  "front, top-down, 3/4 top-down. anims: the "
                                                  "animations, one entry each; `name` is what "
                                                  "the game asks lib/sprites.js for, `action` is "
                                                  "what happens in one sentence, starting and "
                                                  "ending at the rest pose (\"walks in place, "
                                                  "legs alternating\", \"the wheels spin and "
                                                  "the body rocks over bumps\"). facings: with "
                                                  "anims — 4 when the game shows it from the "
                                                  "side and needs front, right, back and left; "
                                                  "1 when one view is all there is (seen from "
                                                  "above, or flat like a card) and the game "
                                                  "rotates it in code. Today only an actor is "
                                                  "animated; anims on any other kind are kept "
                                                  "and it renders still.",
                                              "properties": {
                                                  "body": {"type": "string"},
                                                  "view": {"type": "string"},
                                                  "anims": {"type": "array",
                                                            "items": {"type": "object",
                                                                      "properties": {"name": {"type": "string"},
                                                                                     "action": {"type": "string"}},
                                                                      "required": ["name", "action"]}},
                                                  "facings": {"type": "integer", "enum": [1, 4]}}}},
                   "required": ["id", "kind", "subject", "style"]}}}
WORLD_SCHEMA = {"type": "function", "function": {
    "name": "compose_world",
    "description": ("Have a whole 3D WORLD built into the project: ground with hills and valleys, "
                    "regions of different kinds of land, and the trees, rocks and buildings "
                    "standing on them. Answers with the world's size and its regions in metres, "
                    "and the ground loads from that moment; the scenery keeps rendering into the "
                    "same files while you write the game. Load it with world.js. One world per "
                    "game, and only for a 3D game."),
    "parameters": {"type": "object",
                   "properties": {"description": {"type": "string",
                                                  "description":
                                                      "What the landscape IS, in a sentence or "
                                                      "two: the kinds of land in it and what "
                                                      "stands on them. Never a size, a distance "
                                                      "or a count — the world chooses its own "
                                                      "scale. Describe the place, not the game "
                                                      "played on it."},
                                  "seed": {"type": "integer",
                                           "description": "A different seed is a different "
                                                          "world."}},
                   "required": ["description"]}}}
DONE_SCHEMA = {"type": "function", "function": {
    "name": "done",
    "description": "Call when the project is finished and playable.",
    "parameters": {"type": "object",
                   "properties": {"summary": {"type": "string"}},
                   "required": ["summary"]}}}

SCHEMAS = [LIST_SCHEMA, READ_SCHEMA, WRITE_SCHEMA, EDIT_SCHEMA, MEDIA_SCHEMA,
           WORLD_SCHEMA, DONE_SCHEMA]


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
    message = (result.get("choices") or [{}])[0].get("message", {}) or {}
    content = message.get("content") or ""
    calls = [tc for tc in (message.get("tool_calls") or []) if tc.get("function", {}).get("name")]
    if not calls:
        # The server's parser didn't claim the model's tool-call syntax — recover it from the text.
        calls = parse_tool_calls(content, SCHEMAS)
    cursor.turn += 1

    # A reply that ran out of output tokens saved NOTHING — the tool call was cut off mid-argument.
    # Say so, rather than letting the model believe the file landed.
    if not calls and (usage.get("completion_tokens") or 0) >= cursor.out_cap - 32:
        cursor.no_call_streak += 1
        cursor.history.append({"role": "assistant", "content": content[-2000:]})
        cursor.history.append({"role": "user", "content":
                               "Your last response was cut off by the output token limit, so "
                               f"nothing was saved. {_TOO_BIG}"})
        if cursor.no_call_streak >= _NO_CALL_GIVE_UP:
            return Done(f"stalled: {cursor.no_call_streak} turns cut off by the output limit")
        return _infer(run_dir, cursor)

    if not calls:
        # The same nudge produces the same reply, so each one differs and the streak gives up.
        cursor.no_call_streak += 1
        cursor.history.append({"role": "assistant", "content": content})
        cursor.history.append({"role": "user", "content":
                               _nudge(cursor.no_call_streak, run_dir)})
        if cursor.no_call_streak >= _NO_CALL_GIVE_UP:
            return Done(f"stalled: {cursor.no_call_streak} turns with no tool call")
    else:
        if len(content) > 2000:
            content = "[…analysis truncated…]\n" + content[-2000:]
        cursor.no_call_streak = 0
        cursor.history.append({"role": "assistant", "content": content, "tool_calls": calls})
        for tc in calls:
            if tc["function"]["name"] == "done":
                args = parse_args(tc["function"].get("arguments"))
                if not cursor.done_nudged:
                    # The nudge is the TOOL RESULT, not a user message after it: one message answers
                    # one call, and no round is left with its `tool` half missing.
                    cursor.done_nudged = True
                    cursor.history.append({"role": "tool", "tool_call_id": tc.get("id"),
                                           "content": _done_nudge(run_dir)})
                    continue
                cursor.finished = True
                cursor.summary = _clip(args.get("summary", ""), 400)
                cursor.history.append({"role": "tool", "tool_call_id": tc.get("id"),
                                       "content": "ok"})
                continue
            _apply(tools, cursor, tc)
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


def _action_of(tc, res) -> str:
    """One turn's tool call as a line for the build feed — what the model DID, since the turn
    counter alone says only that it is still going. A failure carries its reason: the whole point
    of watching the feed is seeing the build go wrong before its step cap says so."""
    name = tc["function"]["name"]
    args = parse_args(tc["function"].get("arguments"))
    target = args.get("path") or args.get("id") or ""
    verb = {"write_file": "wrote", "edit_file": "edited", "read_file": "read",
            "generate_media": "asked for art",
            "compose_world": "built a world",
            "list_files": "listed files"}.get(name, name)
    line = f"{verb} {target}".strip()
    if res.get("ok", True):
        return line
    return f"{line} — failed: {_clip(res.get('error') or 'no reason given', 120)}"


def _repeat_note(cursor, tc, res) -> Optional[str]:
    """The error text alone cannot say it has been seen before, so a resent call repeats to the
    step cap.

    Counted per call, not against the previous call alone: a model stuck on one edit re-reads the
    file between attempts, and that succeeding read must not clear the failing edit's count
    (measured 2026-07-29: 12 identical failing edits, every one scored as the first)."""
    sig = hashlib.sha1(
        json.dumps([tc["function"]["name"], parse_args(tc["function"].get("arguments"))],
                   sort_keys=True, default=str).encode("utf-8")).hexdigest()
    if res.get("ok", True):
        cursor.repeat_counts.pop(sig, None)
        return None
    n = cursor.repeat_counts.get(sig, 0) + 1
    cursor.repeat_counts[sig] = n
    if n < 2:
        return None
    return (f"You have now sent this tool call {n} times with exactly identical parameters, and it "
            f"has failed every time. Try something new.")


def _tool_content(res) -> str:
    """Serialized, a file body shows every quote as \\" — which the model then copies into old_text,
    where it matches nothing."""
    if res.get("ok") and "content" in res:
        span = f" lines=\"{res['lines']}\"" if res.get("lines") else ""
        return f"<file path=\"{res.get('path')}\"{span}>\n{res['content']}\n</file>"
    return json.dumps(res)[:_MAX_TOOL_CHARS]


def _cut_off(tc) -> bool:
    """Did this call's argument JSON stop mid-write? The model reached the output cap while
    streaming a big `content`, so the arguments are unterminated and nothing can be recovered from
    them. parse_args answers {} — which reaches the tool as a MISSING argument, and a model told it
    forgot `path` resends the same oversized call (measured 2026-08-01: a 64 KB write_file reported
    as KeyError: 'path', then four turns the server itself refused, then a dead build)."""
    return not parse_args_checked(tc["function"].get("arguments"))[1]


def _apply(tools, cursor, tc) -> None:
    if _cut_off(tc):
        res = {"ok": False, "error": "Your reply hit the output token limit part-way through this "
                                     f"call's arguments, so the call never ran. {_TOO_BIG}"}
        cursor.actions.append(f"{tc['function']['name']} — cut off by the output limit")
        cursor.history.append({"role": "tool", "tool_call_id": tc.get("id"),
                               "content": _tool_content(res)})
        return
    try:
        res = _dispatch(tools, cursor, tc)
    except Exception as e:
        # A tool argument is untrusted input, so each tool validates its own. This is the backstop
        # for the case that slips through: an exception here would otherwise escape the completion
        # handler and strand the build until the reaper re-drives it, with the model never learning
        # what went wrong. It surfaces as a tool error and is logged as the bug it is.
        logger.exception("tool %s raised", tc["function"]["name"])
        res = {"ok": False, "error": f"{tc['function']['name']} failed: {e}"}
    cursor.actions.append(_action_of(tc, res))
    note = _repeat_note(cursor, tc, res)
    if note:
        res = {**res, "error": f"{res.get('error') or ''}\n\n{note}".strip()}
    cursor.history.append({"role": "tool", "tool_call_id": tc.get("id"),
                           "content": _tool_content(res)})


def _dispatch(tools, cursor, tc) -> dict:
    name = tc["function"]["name"]
    fn = tools.get(name)
    if fn is None:
        return {"ok": False, "error": f"unknown tool: {name!r}"}
    return fn(**parse_args(tc["function"].get("arguments")))


def _infer(run_dir, cursor, report: Optional[str] = None) -> Infer:
    ctx = _n_ctx()
    if cursor.prompt_tokens > ctx - _COMPACT_ROOM:
        if compact(run_dir, cursor, int(ctx * _COMPACT_KEEP) * 4):
            cursor.compacted += 1
            cursor.prompt_tokens = 0   # unknown until the server reports the trimmed prompt back
    msgs = MessageBuilder(cursor.system).extend(cursor.history).build()
    # No actions means the turn called no tool: either the opening turn (the prompt has just been
    # sent and nothing has happened yet) or one the nudge is answering.
    report = report or ", ".join(cursor.actions) or (
        _opening_line(cursor) if cursor.turn == 0 else "no tool call — asked again")
    if cursor.compacted:
        report += f" (compacted {cursor.compacted}×)"
    cursor.actions = []
    # The server admits a turn only if prompt + max_tokens fits the window: the cap is whatever the
    # window has left after the prompt. The prompt is the last one the server counted plus this
    # turn's round; counting the whole transcript at 3 chars a token instead would overshoot a
    # near-full window and hand the server a cap below zero.
    chars = lambda ms: sum(len(json.dumps(m)) for m in ms)
    est = (cursor.prompt_tokens + chars(cursor.history[cursor.logged:]) // 3
           if cursor.prompt_tokens else chars(msgs) // 3)
    cursor.out_cap = ctx - est
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


_FILE_RESULT = re.compile(r'^<file path="([^"]*)"(?: lines="([^"]*)")?>\n(.*)\n</file>$', re.S)


# Why a stub states where the bytes are and never says "read it again": told to re-read, the model
# re-reads the whole project after every compaction, and the reads refill the window (measured
# 2026-09-06: two prod builds spent ~150 of 200 steps that way and shipped nothing).
_SUPERSEDED = "a newer copy is later in this transcript"
_ON_DISK = "on disk; not repeated here"


def _write_key(tc: dict) -> Optional[tuple]:
    fn = tc.get("function") or {}
    if fn.get("name") != "write_file":
        return None
    args = parse_args(fn.get("arguments"))
    body = args.get("content")
    return ("write", args.get("path")) if isinstance(body, str) and len(body) >= 200 else None


def _result_key(m: dict) -> Optional[tuple]:
    """A whole-file read keys on the path alone; a window ("5-40/40", or a read cut at the
    ceiling) keys on its span too."""
    hit = _FILE_RESULT.match(m.get("content") or "")
    if not hit or len(hit.group(3)) < 200:
        return None
    span = hit.group(2)
    whole = re.fullmatch(r"1-(\d+)/(\d+)", span or "")
    return ("read", hit.group(1), None if not span or (whole and whole[1] == whole[2]) else span)


def _stub_call(tc: dict, why: str) -> Optional[dict]:
    fn = tc.get("function") or {}
    name = fn.get("name")
    if name not in ("write_file", "edit_file"):
        return None
    args = parse_args(fn.get("arguments"))
    if name == "write_file":
        body = args.get("content")
        if not isinstance(body, str) or len(body) < 200:
            return None
        args["content"] = f"[wrote {args.get('path')}, {len(body)} chars — {why}]"
    else:
        old, new = args.get("old_text"), args.get("new_text")
        if not (isinstance(old, str) and isinstance(new, str)) or len(old) + len(new) < 200:
            return None
        args["old_text"] = f"[{len(old)} chars replaced in {args.get('path')}]"
        args["new_text"] = f"[{len(new)} chars — {why}]"
    return {**tc, "function": {**fn, "arguments": json.dumps(args, ensure_ascii=False)}}


def _stub_result(m: dict, why: str) -> Optional[dict]:
    hit = _FILE_RESULT.match(m.get("content") or "")
    if not hit or len(hit.group(3)) < 200:
        return None
    span = f" lines {hit.group(2)}" if hit.group(2) else ""
    return {**m, "content": f"[read {hit.group(1)}{span}, {len(hit.group(3))} chars — {why}]"}


def dedupe_bodies(history: List[dict]) -> List[dict]:
    """The newest body of each file wins: every older write or read of the same file becomes a
    stub pointing at the newer copy. A read of a line window is its own key, so a window never
    stands in for the whole file, nor the whole for a window. Edits are deltas, not bodies, and
    stay whole — an edit after the live read is what the model changed since."""
    seen: set = set()
    out = []
    for m in reversed(history[1:]):
        if m["role"] == "tool":
            key = _result_key(m)
            if key in seen:
                m = _stub_result(m, _SUPERSEDED) or m
            elif key:
                seen.add(key)
        elif m.get("tool_calls"):
            calls = []
            for tc in m["tool_calls"]:
                key = _write_key(tc)
                if key and (key in seen or ("read", key[1], None) in seen):
                    tc = _stub_call(tc, _SUPERSEDED) or tc
                elif key:
                    seen.add(key)
                calls.append(tc)
            m = {**m, "tool_calls": calls}
        out.append(m)
    return history[:1] + out[::-1]


def trim_bodies(history: List[dict], rounds_to_trim: int) -> List[dict]:
    """The oldest `rounds_to_trim` rounds with their file bodies replaced by a stub naming the path
    and size. The round keeps its shape — what was written, read and edited, in order — and loses
    only bytes that are also on disk."""
    groups = rounds(history)
    out = list(history[:1])
    for i, g in enumerate(groups):
        for m in g:
            if i < rounds_to_trim:
                if m.get("tool_calls"):
                    m = {**m, "tool_calls": [_stub_call(tc, _ON_DISK) or tc for tc in m["tool_calls"]]}
                elif m["role"] == "tool":
                    m = _stub_result(m, _ON_DISK) or m
            out.append(m)
    return out


def compact(run_dir, cursor, keep_chars: int) -> int:
    """Stub every superseded file body; then, oldest round first, stub the bodies out of rounds
    until the tail fits `keep_chars`; only if it still does not fit, drop the OLDEST whole rounds
    and RE-GROUND on the file list.

    A transcript is mostly file bodies that are also on disk, and dropping a round loses the
    model's memory of having written it. Trimmed, the round still says what the model did; the
    bytes come back on `read_file`. The newest rounds are the last to lose their bodies, because
    they are what the model is about to edit against. When rounds do go, the re-grounding matters
    more than the trim: the dropped rounds are where the model watched itself write the files, so
    after one it is editing code it no longer remembers. Returns the rounds trimmed or dropped, 1
    when only superseded bodies went, 0 when nothing changed."""
    size = lambda msgs: sum(len(json.dumps(m)) for m in msgs)
    history = dedupe_bodies(cursor.history)
    deduped = history != cursor.history
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
    note = None
    if dropped:
        root = game_dir(run_dir)
        listing = "\n".join(
            f"{p.relative_to(root)} ({p.stat().st_size} bytes)"
            for p in sorted(root.rglob("*")) if p.is_file() and not p.name.startswith("_")) or "(empty)"
        note = {"role": "user", "content":
                f"[Earlier steps were dropped to save room; {dropped} of them. You cannot see what "
                f"you wrote before, so do not assume — read a file before you edit it.]\n\n"
                f"Files in the project directory right now:\n{listing}"}
    cursor.history = history[:1] + ([note] if note else []) + kept
    # The changed messages are already in the turn log; the record replays the same trim and drop
    # there too, so the archive shows what was really sent rather than the transcript never re-sent.
    turn_log.append_compact(run_dir, turn=cursor.turn, trimmed=trimmed, dropped=dropped,
                            note=note["content"] if note else None)
    cursor.logged = max(1, cursor.logged - removed + (1 if note else 0))
    return trimmed + dropped or 1


def _request_from(spec) -> str:
    """The build's one user message: the run's prompt, byte for byte as stored."""
    return str((spec or {}).get("request") or "").strip() or "Make a small, playable browser game."
