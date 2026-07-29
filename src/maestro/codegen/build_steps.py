"""The build's turn machine, as a resumable step.

`step(spec, run_dir, tools, cursor, result) -> Infer | Done`. With no LLM result to apply yet it
builds and returns the FIRST inference request; otherwise it applies the completed turn's tool calls
and either returns the NEXT request or `Done`. The driver (build_chain) enqueues each `Infer` as one
`llm` job, dies, and re-enters this function on the completion — so the whole loop is spread across
process deaths, its scratch carried in the durable cursor.

The transcript IS the memory: compaction (drop the oldest whole rounds, re-ground on the file
listing) is what keeps it inside the context window.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Union

from llm_clients.message_builder import MessageBuilder
from maestro.codegen.staging import game_dir
from maestro.services import parse_args
from maestro.tool_calls import parse_tool_calls

logger = logging.getLogger(__name__)

_PROMPTS = Path(__file__).resolve().parent / "prompts"

MAX_TURNS = 80
MAX_TOKENS = 16_000
# Compact when the last prompt crossed this fraction of the window, leaving room for the reply and
# the next tool result; keep this fraction of it afterwards.
_COMPACT_AT = 0.62
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


def _nudge(streak: int) -> str:
    return _NUDGES[min(streak, len(_NUDGES)) - 1]


# ── step outcomes ─────────────────────────────────────────────────────────────
@dataclass
class Infer:
    """Suspend here: enqueue one `llm` job with these messages, die, resume on completion."""
    messages: List[dict]
    schemas: List[dict]
    max_tokens: int
    # "none" is the floor. Passing None instead reaches the connector as an explicit "let the model
    # pick", which skips the enable_thinking switch — a measured turn then spent 16000 tokens
    # reasoning and never called a tool.
    reasoning: Optional[str] = "none"
    report: Optional[str] = None      # progress line emitted when this turn is enqueued


@dataclass
class Done:
    report: str


Outcome = Union[Infer, Done]


# ── tool schemas ──────────────────────────────────────────────────────────────
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
                   "properties": {"path": {"type": "string"}},
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
    "description": ("Have an artist draw an image or a 3D model for the game. Returns the path the "
                    "file will appear at, right away — the drawing itself takes about a minute."),
    "parameters": {"type": "object",
                   "properties": {"id": {"type": "string",
                                         "description": "Short name: letters, digits, - and _."},
                                  "prompt": {"type": "string",
                                             "description": "What to draw, described for an artist."},
                                  "kind": {"type": "string", "enum": ["image", "mesh"],
                                           "description": "image (a .png) or mesh (a .glb model)."}},
                   "required": ["id", "prompt"]}}}
DONE_SCHEMA = {"type": "function", "function": {
    "name": "done",
    "description": "Call when the project is finished and playable.",
    "parameters": {"type": "object",
                   "properties": {"summary": {"type": "string"}},
                   "required": ["summary"]}}}

SCHEMAS = [LIST_SCHEMA, READ_SCHEMA, WRITE_SCHEMA, EDIT_SCHEMA, MEDIA_SCHEMA, DONE_SCHEMA]


def _n_ctx() -> int:
    from config.settings_manager import settings_manager
    return int((settings_manager.get_settings().get("llm") or {}).get("n_ctx") or 32768)


# ── the turn ──────────────────────────────────────────────────────────────────
def step(spec, run_dir, tools, cursor, result) -> Outcome:
    """One turn: apply the completed turn's tool calls, then ask for the next."""
    if not cursor.started:
        cursor.started = True
        cursor.system = (_PROMPTS / "build.txt").read_text(encoding="utf-8")
        cursor.history = [{"role": "user", "content": cursor.request or _request_from(spec)}]
        return _infer(run_dir, cursor)

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
    if not calls and (usage.get("completion_tokens") or 0) >= MAX_TOKENS - 32:
        cursor.history.append({"role": "assistant", "content": content[-2000:]})
        cursor.history.append({"role": "user", "content":
                               "Your last response was cut off by the output token limit, so "
                               "nothing was saved. Write the file in smaller pieces: split the game "
                               "across several files, or write one section at a time. Do not repeat "
                               "a whole large file to change a small part of it."})
        return _infer(run_dir, cursor)

    if not calls:
        # The same nudge produces the same reply, so each one differs and the streak gives up.
        cursor.no_call_streak += 1
        cursor.history.append({"role": "assistant", "content": content})
        cursor.history.append({"role": "user", "content": _nudge(cursor.no_call_streak)})
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


def _action_of(tc, res) -> str:
    """One turn's tool call as a line for the build feed — what the model DID, since the turn
    counter alone says only that it is still going. A failure carries its reason: the whole point
    of watching the feed is seeing the build go wrong before its step cap says so."""
    name = tc["function"]["name"]
    args = parse_args(tc["function"].get("arguments"))
    target = args.get("path") or args.get("id") or ""
    verb = {"write_file": "wrote", "edit_file": "edited", "read_file": "read",
            "generate_media": "asked for art", "list_files": "listed files"}.get(name, name)
    line = f"{verb} {target}".strip()
    if res.get("ok", True):
        return line
    return f"{line} — failed: {_clip(res.get('error') or 'no reason given', 120)}"


def _apply(tools, cursor, tc) -> None:
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
    cursor.history.append({"role": "tool", "tool_call_id": tc.get("id"),
                           "content": json.dumps(res)[:20_000]})


def _dispatch(tools, cursor, tc) -> dict:
    name = tc["function"]["name"]
    args = parse_args(tc["function"].get("arguments"))
    fn = tools.get(name)
    if fn is None:
        res = {"ok": False, "error": f"unknown tool: {name!r}"}
    elif name == "read_file":
        res = fn(path=args.get("path"))
    elif name == "write_file":
        res = fn(path=args.get("path"), content=args.get("content"))
    elif name == "edit_file":
        res = fn(path=args.get("path"), old_text=args.get("old_text"),
                 new_text=args.get("new_text"))
    elif name == "generate_media":
        res = fn(id=args.get("id"), prompt=args.get("prompt"), kind=args.get("kind"))
    else:
        res = fn()
    return res


def _infer(run_dir, cursor) -> Infer:
    ctx = _n_ctx()
    if cursor.prompt_tokens > int(ctx * _COMPACT_AT):
        dropped = compact(run_dir, cursor, int(ctx * _COMPACT_KEEP) * 4)
        if dropped:
            cursor.compacted += dropped
            cursor.prompt_tokens = 0   # unknown until the server reports the trimmed prompt back
    msgs = MessageBuilder(cursor.system).extend(cursor.history).build()
    # No actions means the turn called no tool: either the opening turn (the prompt has just been
    # sent and nothing has happened yet) or one the nudge is answering.
    report = ", ".join(cursor.actions) or (
        "sent the prompt" if cursor.turn == 0 else "no tool call — asked again")
    if cursor.compacted:
        report += f" ({cursor.compacted} round(s) compacted)"
    cursor.actions = []
    return Infer(msgs, SCHEMAS, MAX_TOKENS, report=report)


# ── compaction ────────────────────────────────────────────────────────────────
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


def compact(run_dir, cursor, keep_chars: int) -> int:
    """Drop the OLDEST whole rounds until the tail fits, then RE-GROUND on the file list.

    Re-grounding matters more than the trim: the dropped rounds are where the model watched itself
    write the files, so after a trim it is editing code it no longer remembers. The listing costs a
    few dozen tokens and turns "edit blind" back into "read, then edit"."""
    groups = rounds(cursor.history)
    total = sum(len(json.dumps(m)) for g in groups for m in g)
    dropped = 0
    while groups and total > keep_chars:
        g = groups.pop(0)
        total -= sum(len(json.dumps(m)) for m in g)
        dropped += 1
    if not dropped:
        return 0
    root = game_dir(run_dir)
    listing = "\n".join(
        f"{p.relative_to(root)} ({p.stat().st_size} bytes)"
        for p in sorted(root.rglob("*")) if p.is_file() and not p.name.startswith("_")) or "(empty)"
    note = {"role": "user", "content":
            f"[Earlier steps were dropped to save room; {dropped} of them. You cannot see what you "
            f"wrote before, so do not assume — read a file before you edit it.]\n\n"
            f"Files in the project directory right now:\n{listing}"}
    cursor.history = cursor.history[:1] + [note] + [m for g in groups for m in g]
    return dropped


def _request_from(spec) -> str:
    """The build's one user message: the run's prompt, byte for byte as the human approved it."""
    return str((spec or {}).get("request") or "").strip() or "Make a small, playable browser game."
