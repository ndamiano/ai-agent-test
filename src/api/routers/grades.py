"""The grading surface: the owner plays a built game and writes down what he thought of it.

`docs/game_rubric.md` is the form and the reasoning behind its shape; `grading.py` is the store.
This router only carries them to a browser, and it has one job of its own — keeping the grader
BLIND. The page is handed the run's request so the reveal step can show it, and nothing else: not
the model, not the arm, not whether the run was staged, not how many turns or fix rounds it took.
A grade formed while knowing which arm produced the build is not evidence about the arm.

Operator-only, and ownership still applies — the play session the page mints goes through the same
owner check every other play does, so this is not a way to reach someone else's game.
"""

import logging
from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

import grading
from auth.deps import require_admin
from auth.store import User
from db import store as db_store
from maestro.state import RunState

logger = logging.getLogger("grades")

router = APIRouter()


class Claim(BaseModel):
    claim: str
    verdict: Literal["delivered", "partial", "absent"]
    note: str = ""


class Grade(BaseModel):
    """The filled form. Shapes are pinned here because this is a system boundary; what the fields
    MEAN is the rubric's business, and the server deliberately computes nothing from them — there
    is no total, because the dimensions do not add up to the verdict."""
    loads: bool
    takes_input: bool
    crashed: bool = False
    crash_note: str = ""
    first_impression: Optional[int] = None
    first_impression_note: str = ""
    show_someone: str = ""
    what_is_it: str = ""
    biggest_gap: str = ""
    dimensions: Dict[str, Dict[str, Any]] = {}   # name -> {score: int|null, note: str}
    considered: Optional[int] = None
    considered_note: str = ""
    what_moved_it: str = ""
    claims: List[Claim] = []
    unrequested: str = ""
    play_ended: str = ""
    play_minutes: Optional[int] = None


def _owned_spec(run_id: str, user: User) -> Dict[str, Any]:
    """The run's spec, scoped to its owner. One 404 for "no such run" and for "not yours" — an
    operator surface still has no business confirming another account's run ids."""
    if not grading.RUN_ID.match(run_id):
        raise HTTPException(status_code=404, detail="no such game")
    spec = RunState(run_id).read_spec()
    if spec is None or db_store.owner_of(run_id) != user.id:
        raise HTTPException(status_code=404, detail="no such game")
    return spec


@router.get("", response_model=Dict)
async def all_grades(_: User = Depends(require_admin)) -> Dict[str, Any]:
    """Every grade taken, for the side-by-side. Unlike the grading page this one carries the
    revision that built each game — the whole point here is to see whether a change to the loop
    moved anything, which needs the pipeline version visible."""
    return {"grades": grading.everything()}


def _built_by(run_id: str) -> str:
    """The revision of the LAST build to touch this game — a fix build is part of what is being
    graded, so the newest wins rather than the one that started it."""
    revs = [b.get("maestro_rev") for b in db_store.builds_for(run_id) if b.get("maestro_rev")]
    return revs[-1] if revs else "unknown"


@router.get("/{run_id}", response_model=Dict)
async def grade_target(run_id: str, user: User = Depends(require_admin)) -> Dict[str, Any]:
    """What the grading page is allowed to know. The request rides along because the page needs it
    at the reveal step; everything that would identify the arm stays here."""
    spec = _owned_spec(run_id, user)
    return {
        "run_id": run_id,
        "request": spec.get("request", ""),
        "previous": len(grading.history(run_id)),
    }


@router.post("/{run_id}", response_model=Dict)
async def submit_grade(run_id: str, body: Grade,
                       user: User = Depends(require_admin)) -> Dict[str, Any]:
    _owned_spec(run_id, user)
    name = grading.save(run_id, body.model_dump(), maestro_rev=_built_by(run_id))
    return {"saved": name}

