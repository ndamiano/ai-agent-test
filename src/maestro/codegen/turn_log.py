"""The build's TURN LOG — every llm turn a run spent, appended to the run dir that owns the game.

A build turn's request IS the whole transcript so far, so a jobs row per turn stores the same
conversation once per turn (measured 2026-07-31: 951 MB of `jobs.payload`, 26 MB for one 117-turn
build). This log stores each message ONCE: the system prompt and the eight tool schemas are
byte-identical on every turn of a build, so they ride a single `meta` record, and a `turn` record
carries only what that turn ADDED. Turn k is exactly `meta.system` + `meta.tools` +
`concat(added[0..k])`.

Three records, one JSON object per line, appended and never rewritten:

    meta     once per build — a FIX re-enters the same run dir, so it appends its own meta under
             the new build_id and the transcript restarts there
    turn     one per COMPLETED turn: what it added, what came back, what it cost
    compact  the rounds `build_steps.compact` trimmed and dropped from the live transcript plus
             the note it re-grounded on, replayed at read time so a reconstruction shows what was really sent

The line lands BEFORE the jobs row's copy is cleared. The append and the sqlite write cannot share
a transaction, so the ORDER is the whole guarantee: a failure between them wastes disk, never the
only copy.

The run dir is self-contained on purpose — it is the archive, and it has to be movable as one
folder.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Tuple

FILE = "turns.jsonl"


def path(run_dir) -> Path:
    return Path(run_dir) / FILE


def append_meta(run_dir, *, run_id: str, build_id: str, system: str, tools: List[Dict],
                wire: str, model: Optional[str], max_tokens: int, reasoning: Optional[str]) -> None:
    """Everything a build repeats on every turn, once. `wire` names the dialect the messages below
    are written in, so a reader never has to ask the current connector what shape they are in."""
    _append(run_dir, {"kind": "meta", "run_id": run_id, "build_id": build_id, "system": system,
                      "tools": tools, "wire": wire, "model": model, "max_tokens": max_tokens,
                      "reasoning": reasoning})


def append_turn(run_dir, *, turn: int, job_id: str, added: List[Dict], response: Optional[Dict],
                usage: Dict, exec_seconds: float, error: Optional[str]) -> None:
    """One completed turn. `added` is the slice of transcript that preceded this turn's request —
    the previous turn's reply and its tool results — so appending them in order rebuilds every
    request the build ever sent."""
    _append(run_dir, {"kind": "turn", "turn": turn, "job_id": job_id, "added": added,
                      "response": response, "usage": usage, "exec_seconds": exec_seconds,
                      "error": error})


def append_compact(run_dir, *, turn: int, trimmed: int, dropped: int, note: Optional[str]) -> None:
    _append(run_dir, {"kind": "compact", "turn": turn, "trimmed": trimmed, "dropped": dropped,
                      "note": note})


def _append(run_dir, record: Dict) -> None:
    with path(run_dir).open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
        f.flush()
        os.fsync(f.fileno())


def read_turn(run_dir, job_id: str) -> Optional[Dict]:
    """One archived turn: {meta, messages, response, usage, exec_seconds, error}, with `messages`
    the exact list that turn's request carried. None when this run's log has no such turn."""
    for meta, record, messages, _chars in _replay(run_dir):
        if record["job_id"] == job_id:
            return {"meta": meta, "messages": list(messages), "response": record["response"],
                    "usage": record["usage"] or {}, "exec_seconds": record["exec_seconds"],
                    "error": record["error"]}
    return None


def _replay(run_dir) -> Iterator[Tuple[Dict, Dict, List[Dict], int]]:
    """Walk the log, rebuilding the transcript as the build built it. Yields
    (meta, turn record, messages as that turn sent them, their serialized size)."""
    p = path(run_dir)
    if not p.exists():
        return
    meta: Dict = {}
    messages: List[Dict] = []
    chars = 0
    with p.open(encoding="utf-8") as f:
        for line in f:
            record = json.loads(line)
            kind = record["kind"]
            if kind == "meta":
                # A fix builds the same run dir from an empty transcript, so its meta starts one.
                meta, messages, chars = record, [], 0
            elif kind == "turn":
                messages = messages + record["added"]
                chars += _chars_of(record["added"])
                yield meta, record, messages, chars
            elif kind == "compact":
                messages = _compacted(messages, record)
                chars = _chars_of(messages)


def _compacted(messages: List[Dict], record: Dict) -> List[Dict]:
    """Replay one compaction exactly as build_steps.compact performed it: thinking dropped,
    superseded file bodies stubbed, then the bodies out of the oldest rounds, then the oldest whole
    rounds gone with the re-grounding note in their place, the request kept."""
    from maestro.codegen import build_steps   # module-level would cycle: build_steps writes here
    messages = build_steps.drop_read_only_rounds(
        build_steps.dedupe_bodies(build_steps.strip_reasoning(messages)))
    if record["trimmed"]:
        messages = build_steps.trim_bodies(messages, record["trimmed"])
    groups = build_steps.rounds(messages)
    kept = [m for g in groups[record["dropped"]:] for m in g]
    return messages[:1] + [{"role": "user", "content": record["note"]}] + kept


def _chars_of(messages: List[Dict]) -> int:
    return sum(len(json.dumps(m, ensure_ascii=False)) for m in messages)
