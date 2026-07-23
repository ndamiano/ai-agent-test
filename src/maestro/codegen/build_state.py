"""The durable build cursor — everything the executor used to hold in memory, on disk.

The build is no longer a resident loop. Each LLM turn is a job on the `llm` queue; the process
that started the turn dies, and the job's completion (`/worker/complete` → build_chain) drives the
NEXT turn. So the loop's live state — where we are (outer gate sweep vs mid-fix), the step count,
the cross-fix stall/park bookkeeping, and the current fix's growing transcript — must survive across
process death. It lives here, in `runs/<id>/build_state.json`, the same run dir that already owns
the spec and the game source. Job metadata carries only `{stage, run_id, build_id}`; this file is
the single source of truth the completion reloads, advances, and rewrites.

`tool_versions`/`tool_seen` are the read→edit grounding (`build_codegen_tools`' in-memory closure
in the old world). A resumed fix's `edit` would otherwise be refused ("you didn't read before
editing") because a fresh process forgot the read — so the grounding persists here too and is
rehydrated into the tools each completion.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from maestro.modules.module import Error, ErrorType

_STATE_FILE = "build_state.json"


# ── Error <-> plain dict (the cursor is JSON) ─────────────────────────────────
def error_to_dict(error: Error) -> Dict:
    return {"type": error.type.value, "code": error.code, "component": error.component,
            "message": error.message, "path": error.path, "ref": error.ref, "kind": error.kind}


def error_from_dict(d: Dict) -> Error:
    return Error(type=ErrorType(d["type"]), code=d["code"], component=d["component"],
                 message=d["message"], path=d.get("path"), ref=d.get("ref"), kind=d.get("kind"))


def snapshot(pairs) -> List[str]:
    """The stall-detection key for a to-do list: its error identities, order-independent. A list
    (not a set) so it survives JSON; compared as sets."""
    from maestro.modules.module import idkey
    return sorted(idkey(e) for _, e in pairs)


@dataclass
class FixCursor:
    """The state of the in-progress fix. `shape` selects the step machine (plan/data/author/
    read_write); the rest is that machine's scratch — an author retry counter, or the read→edit
    loop's transcript + counters. `history` IS the transcript the user pointed at: turn N's messages,
    grown each turn, re-sent to the worker verbatim."""
    shape: str
    error: Dict                                   # error_to_dict form
    escalate: bool = False
    report: str = ""                              # last human-facing progress line for this fix
    # author retry
    attempt: int = 0
    # read_write subloop
    turn: int = 0
    nreads: int = 0
    edit_fails: int = 0
    wrote: Optional[str] = None
    mode: Optional[str] = None
    system: str = ""                              # the fix's system prompt, re-sent each turn
    history: List[Dict] = field(default_factory=list)
    started: bool = False                         # has the first request been built yet


@dataclass
class BuildCursor:
    build_id: str
    kind: str = "build"                           # "build" | "fix" (human-note seeded)
    phase: str = "outer"                          # "outer" | "fix" | "done"
    step: int = 0
    max_steps: int = 60
    t0: float = 0.0
    ok: Optional[bool] = None
    # outer stall/park/milestone bookkeeping (was AgentLoop._recent/_parked/prev/passed)
    recent: List[List[str]] = field(default_factory=list)   # last N to-do snapshots
    parked: List[str] = field(default_factory=list)         # idkeys the loop gave up on
    prev: Optional[List[str]] = None                        # snapshot before the current/last fix
    passed: List[str] = field(default_factory=list)         # components already milestoned
    todo: List[Dict] = field(default_factory=list)   # last outer to-do (emitted with build_step)
    # the current fix
    fix: Optional[Dict] = None                    # FixCursor as dict
    # read→edit grounding, durable across process death
    tool_versions: Dict[str, int] = field(default_factory=dict)
    tool_seen: Dict[str, int] = field(default_factory=dict)

    def fix_cursor(self) -> Optional[FixCursor]:
        return FixCursor(**self.fix) if self.fix else None

    def set_fix(self, fc: Optional[FixCursor]) -> None:
        self.fix = asdict(fc) if fc else None


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
