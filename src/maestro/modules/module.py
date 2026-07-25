"""Module — the gate contract a game build is swept against.

A module IS a list of `Check`s — each a `detect` that reports 0..N `Error`s over the shared context.
The base sweeps them (`get_errors`) in declared order, honouring `blocking` (a hard dependency tier)
and `when_clean` (a terminal check). Detection is all that lives here now: the FIX for each error is
owned by the build driver (build_chain → build_steps), keyed off `Error.code`, not by the module.
"""

from __future__ import annotations

import json
from abc import ABC
from dataclasses import dataclass
from enum import Enum
from typing import Callable, List, Optional, Tuple


class ErrorType(Enum):
    HUMAN = "human"
    BUILD = "build"
    FIX = "fix"


@dataclass(frozen=True)
class Error:
    type: ErrorType
    code: str                 # check type, e.g. "typechecks" / "plays" — the identity axis
    component: str            # the on-disk component the failure lands in
    message: str              # human-facing description; reword-safe, outside identity()
    path: Optional[str] = None      # locator within the component (filename)
    ref: Optional[str] = None       # for reference errors: the unresolved id
    kind: Optional[str] = None      # violation kind (crash phase, tsc shape) — lets a fix class route

    def identity(self) -> Tuple:
        """Stable key the loop compares across steps for stall detection, across rewordings of
        `message`. All-string so tuples are totally orderable (None path/ref would TypeError
        against a str one in `prioritize`)."""
        return (self.type.value, self.code, self.component, self.path or "", self.ref or "")


def idkey(error: "Error") -> str:
    """A JSON-serializable form of `Error.identity()` — the durable key a human waiver is stored and
    matched under, and the key the build cursor snapshots for stall detection."""
    return json.dumps([error.type.value, error.code, error.component, error.path, error.ref],
                      ensure_ascii=False)


# ── The (detector) a module is a list of ─────────────────────────────────────
@dataclass(frozen=True)
class Check:
    """One gate: a `detect` plus the sweep flags that order it. `detect(check, module, context) ->
    [Error]` reports 0..N errors (a clean check returns []). `code` is the error identity axis the
    fix driver routes on.

    Sweep order is the declared list order. `blocking` = if this check emits, stop and suppress every
    later check (a hard dependency tier). `when_clean` = skip this check unless nothing has been
    emitted yet (a terminal check — the runtime gates are only meaningful once typecheck passes)."""
    code: str
    detect: Callable                       # (check, module, context) -> [Error]
    job: str = "author"                    # author => BUILD tier, else FIX (unless `tier` is set)
    tier: Optional[ErrorType] = None       # error type; defaults from `job`
    blocking: bool = False
    when_clean: bool = False

    def __post_init__(self):
        if self.tier is None:
            object.__setattr__(self, "tier",
                               ErrorType.BUILD if self.job == "author" else ErrorType.FIX)


class Module(ABC):
    """One buildable unit — a list of `Check`s the base sweeps. Subclasses declare `checks` and
    (optionally) `component`; they override no method."""

    id: str
    priority: int = 100   # order within an error tier; lower acts first
    component: str = ""
    checks: List[Check] = []

    def get_errors(self, context) -> List[Error]:
        """Sweep the checks in declared order, accumulating their errors. A `blocking` check that
        emits stops the sweep (its tier is a hard dependency for everything below); a `when_clean`
        check is skipped once anything has been emitted (a terminal check)."""
        errs: List[Error] = []
        for chk in self.checks:
            if chk.when_clean and errs:
                continue
            got = chk.detect(chk, self, context)
            errs += got
            if chk.blocking and got:
                break
        return errs

    def _check_for(self, code: str) -> Optional[Check]:
        return next((c for c in self.checks if c.code == code), None)

    def check_rank(self, code: str) -> int:
        """The declared position of an error's check — the fix-priority axis within this module.
        Declaration order is the author's intent (author before wire before polish)."""
        return next((i for i, c in enumerate(self.checks) if c.code == code), len(self.checks))

    def affected_components(self) -> Tuple[str, ...]:
        return (self.component,) if self.component else ()
