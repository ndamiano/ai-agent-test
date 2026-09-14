"""The durable build cursor — everything the driver would otherwise hold in memory, on disk.

Each LLM turn is a job on the `llm` queue; the process that started the turn dies, and the job's
completion (`/worker/complete` → build_chain) drives the NEXT turn. So the loop's live state — the
step count, the growing transcript, the read→edit grounding — must survive process death. It lives
in `runs/<id>/build_state.json`, the same run dir that owns the spec and the game folder. Job
metadata carries only `{stage, run_id, build_id}`; this file is the single source of truth the
completion reloads, advances, and rewrites.
"""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

_STATE_FILE = "build_state.json"
DEFAULT_MAX_STEPS = 200


@dataclass
class BuildCursor:
    build_id: str
    kind: str = "build"                           # "build" | "fix" | "change"
    phase: str = "build"                          # "build" | "checking" (a gate is running) | "done"
    step: int = 0
    max_steps: int = DEFAULT_MAX_STEPS
    paused: bool = False
    t0: float = 0.0
    ok: Optional[bool] = None
    request: str = ""                             # overrides the spec-rendered request (a gate fix or a change note)
    started: bool = False
    turn: int = 0
    # what the SERVER reported for the last turn — an estimate of ours that drifts low would hit the
    # context window with no warning.
    prompt_tokens: int = 0
    # chars per token of the last prompt the server counted: compaction measures chars, and its
    # target is a share of the window in tokens.
    chars_per_token: float = 0.0
    compacted: int = 0                            # compactions so far
    no_call_streak: int = 0
    redriven: int = 0
    out_cap: int = 0
    # hashed failing tool call -> times sent. Per call, not just the last one: a stuck model
    # re-reads between retries, and that read must not clear the failing edit's count.
    repeat_counts: Dict[str, int] = field(default_factory=dict)
    actions: List[str] = field(default_factory=list)   # last turn's tool calls, for the build feed
    finished: bool = False                        # the model called `done`
    summary: str = ""
    system: str = ""
    history: List[Dict] = field(default_factory=list)
    # The turn log's place in that history: how many messages have been archived to turns.jsonl,
    # and whether this build's `meta` record is down. Both durable — a control plane that restarts
    # mid-build must not re-append what a previous process already wrote.
    logged: int = 0
    meta_logged: bool = False


def _path(run_dir: Path) -> Path:
    return Path(run_dir) / _STATE_FILE


def load(run_dir) -> Optional[BuildCursor]:
    p = _path(run_dir)
    if not p.exists():
        return None
    return BuildCursor(**json.loads(p.read_text(encoding="utf-8")))


def save(run_dir, cursor: BuildCursor) -> None:
    # Atomic like RunState._write: a torn build_state.json would strand a build unrecoverably.
    p = _path(run_dir)
    tmp = p.parent / f".{_STATE_FILE}.{uuid.uuid4().hex}.tmp"
    tmp.write_text(json.dumps(asdict(cursor), indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, p)


def clear(run_dir) -> None:
    _path(run_dir).unlink(missing_ok=True)
