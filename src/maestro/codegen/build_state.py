"""The durable build cursor — everything the driver would otherwise hold in memory, on disk.

Each LLM turn is a job on the `llm` queue; the process that started the turn dies, and the job's
completion (`/worker/complete` → build_chain) drives the NEXT turn. So the loop's live state — the
step count, the growing transcript, the read→edit grounding — must survive process death. It lives
in `runs/<id>/build_state.json`, the same run dir that owns the spec and the game folder. Job
metadata carries only `{stage, run_id, build_id}`; this file is the single source of truth the
completion reloads, advances, and rewrites.

`tool_versions`/`tool_seen` are the read→edit grounding. A resumed `edit` would otherwise be refused
("you didn't read before editing") because a fresh process forgot the read.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

_STATE_FILE = "build_state.json"


@dataclass
class AuditCursor:
    """One claim at a time through a read→verdict subloop. history/turn/nreads reset per claim;
    verdicts accumulates every claim's outcome for the durable log."""
    claim_idx: int = 0
    turn: int = 0
    nreads: int = 0
    system: str = ""
    history: List[Dict] = field(default_factory=list)
    verdicts: List[Dict] = field(default_factory=list)
    findings: List[Dict] = field(default_factory=list)
    delivered: List[str] = field(default_factory=list)
    anchors: List[str] = field(default_factory=list)


@dataclass
class BuildCursor:
    build_id: str
    kind: str = "build"                           # "build" | "fix"
    phase: str = "build"                          # "build" | "audit" | "done"
    step: int = 0
    max_steps: int = 200
    t0: float = 0.0
    ok: Optional[bool] = None
    request: str = ""                             # overrides the spec-rendered request (a fix note)
    # the build turn machine
    started: bool = False
    turn: int = 0
    nreads: int = 0
    edit_fails: int = 0
    # what the SERVER reported for the last turn — an estimate of ours that drifts low would hit the
    # context window with no warning.
    prompt_tokens: int = 0
    compacted: int = 0                            # transcript rounds dropped so far
    no_call_streak: int = 0                       # consecutive turns that produced no tool call
    finished: bool = False                        # the model called `done`
    summary: str = ""
    system: str = ""
    history: List[Dict] = field(default_factory=list)
    # the spec-vs-code audit, run once after the build finishes
    audit: Optional[Dict] = None
    audit_done: bool = False
    audit_delivered: List[str] = field(default_factory=list)
    # the asset batch, once the game's assets.json has been enqueued
    asset_batch: Optional[str] = None
    # read→edit grounding, durable across process death
    tool_versions: Dict[str, int] = field(default_factory=dict)
    tool_seen: Dict[str, int] = field(default_factory=dict)

    def audit_cursor(self) -> Optional[AuditCursor]:
        return AuditCursor(**self.audit) if self.audit else None

    def set_audit(self, ac: Optional[AuditCursor]) -> None:
        self.audit = asdict(ac) if ac else None


# ── persistence ───────────────────────────────────────────────────────────────
def _path(run_dir: Path) -> Path:
    return Path(run_dir) / _STATE_FILE


def load(run_dir) -> Optional[BuildCursor]:
    p = _path(run_dir)
    if not p.exists():
        return None
    return BuildCursor(**json.loads(p.read_text(encoding="utf-8")))


def save(run_dir, cursor: BuildCursor) -> None:
    # Atomic like RunState._write: a torn build_state.json would strand a build unrecoverably.
    import os
    import uuid
    p = _path(run_dir)
    tmp = p.parent / f".{_STATE_FILE}.{uuid.uuid4().hex}.tmp"
    tmp.write_text(json.dumps(asdict(cursor), indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, p)


def clear(run_dir) -> None:
    _path(run_dir).unlink(missing_ok=True)
