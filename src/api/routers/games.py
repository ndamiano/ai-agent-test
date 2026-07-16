"""Games router — browse and drive codegen build runs.

A "game" is one run dir under <working_directory>/runs/<run_id>/. The list is a cheap spec read
per row. The detail view derives live status from the build queue + the run control. Freeze, build
(queued on the single GPU), pause/resume, fix-from-note, and asset skinning all live here.
"""

import logging
import threading
from typing import Dict, List

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from api.build_queue import build_queue, AlreadyQueued
from auth.deps import get_current_user
from auth.store import User
from maestro.codegen.gates import RUNTIME_DIR, game_dir

logger = logging.getLogger(__name__)
router = APIRouter()


class BuildBody(BaseModel):
    auto_pause: bool = False


class AutoPauseBody(BaseModel):
    enabled: bool


class FixBody(BaseModel):
    note: str = ""


# Fix / asset jobs in flight (one per run), so the endpoints double-fire guard without racing the
# build queue (which serializes builds). A daemon thread runs each; the set gates re-entry.
_active_lock = threading.Lock()
_active: set = set()


def _runs_dir():
    from tools.execution_context import resolve_base_path
    return resolve_base_path() / "runs"


def _built(run_id: str) -> bool:
    return (RUNTIME_DIR / "games" / run_id / "main.js").exists()


def _require_state(run_id: str, user: User):
    """The run's state, scoped to its owner: 404 if there's no spec, 403 if it isn't this user's."""
    from maestro.state import RunState

    state = RunState.for_run(run_id)
    if state.read_spec() is None:
        raise HTTPException(status_code=404, detail=f"no game {run_id!r}")
    if state.read_owner() != user.id:
        raise HTTPException(status_code=403, detail="not your game")
    return state


@router.get("", response_model=List[Dict])
async def list_games(user: User = Depends(get_current_user)):
    """Lightweight summary of the caller's runs (one spec read per row)."""
    from maestro.state import RunState

    runs = _runs_dir()
    if not runs.exists():
        return []

    games: List[Dict] = []
    for run_dir in runs.iterdir():
        if not run_dir.is_dir():
            continue
        state = RunState(run_dir)
        spec = state.read_spec()
        if spec is None:
            continue
        if state.read_owner() != user.id:
            continue
        games.append({
            "run_id": run_dir.name,
            "title": spec.get("title", ""),
            "mode": spec.get("mode", ""),
            "frozen": bool(spec.get("frozen")),
            "built": _built(run_dir.name),
            "building": build_queue.is_active(run_dir.name),
            "mtime": run_dir.stat().st_mtime,
        })

    games.sort(key=lambda g: g["mtime"], reverse=True)
    return games


@router.get("/{run_id}", response_model=Dict)
async def get_game(run_id: str, user: User = Depends(get_current_user)):
    """Full detail for one game: the freeform spec, built/building state, and live status."""
    from maestro.run_control import get as get_control

    state = _require_state(run_id, user)
    spec_data = state.read_spec()
    ctrl = get_control(run_id)
    built = _built(run_id)
    qstate = build_queue.state_of(run_id)
    if qstate and qstate["status"] == "queued":
        status, queue_position = "queued", qstate["position"]
    elif qstate:
        status, queue_position = ("paused" if ctrl and ctrl.paused else "building"), None
    else:
        status, queue_position = ("built" if built else "idle"), None
    return {
        "run_id": run_id,
        "spec": spec_data,
        "mode": spec_data.get("mode", ""),
        "frozen": bool(spec_data.get("frozen")),
        "built": built,
        "building": qstate is not None,
        "status": status,
        "queue_position": queue_position,
        "auto_pause": ctrl.auto_pause if ctrl else False,
        "assets_exist": (game_dir(state.run_dir) / "assets.json").exists(),
        "play_url": f"/play/index.html?game={run_id}" if built else None,
    }


@router.post("/{run_id}/freeze", response_model=Dict)
async def freeze_game(run_id: str, user: User = Depends(get_current_user)):
    """Human approval action — freeze the spec so the build can run."""
    import asyncio
    from maestro.codegen.run import freeze_spec

    _require_state(run_id, user)
    return await asyncio.to_thread(freeze_spec, run_id)


@router.post("/{run_id}/build", response_model=Dict)
async def build_game(run_id: str, body: BuildBody = BuildBody(),
                     user: User = Depends(get_current_user)):
    """Queue a build on the single GPU. It runs immediately if the worker is free, else it waits
    with a `queue_position`. Progress streams over the websocket.

    A run is charged ONCE, gated on a durable `charged` flag: the first enqueue deducts
    `cost(spec)`; every later enqueue for the same run (a re-trigger, a resume after a dead build)
    finds it already flagged and never re-charges. Charged stays charged — there is no automatic
    refund."""
    state = _require_state(run_id, user)
    spec_data = state.read_spec()
    if not spec_data.get("frozen"):
        raise HTTPException(status_code=400, detail="freeze the spec before building")

    from auth import store
    from auth.billing import cost

    if not state.is_charged():
        price = cost(spec_data)
        if not store.deduct(user.id, price, "build", run_id):
            raise HTTPException(status_code=402, detail={
                "reason": "insufficient_credits", "balance": store.balance(user.id), "cost": price})
        state.mark_charged()

    try:
        position = build_queue.enqueue(run_id, user.id, body.auto_pause)
    except AlreadyQueued:
        raise HTTPException(status_code=409, detail="build already in progress")

    return {"status": "building" if position == 0 else "queued",
            "run_id": run_id, "queue_position": position}


def _control(run_id: str):
    """The live control for an in-flight build, or 409 if nothing is building."""
    from maestro.run_control import get

    ctrl = get(run_id)
    if ctrl is None:
        raise HTTPException(status_code=409, detail="no build in progress for this run")
    return ctrl


@router.post("/{run_id}/pause", response_model=Dict)
async def pause_game(run_id: str, user: User = Depends(get_current_user)):
    """Pause a running build — it halts at the next step boundary (state stays consistent)."""
    _require_state(run_id, user)
    _control(run_id).request_pause()
    return {"run_id": run_id, "status": "pausing"}


@router.post("/{run_id}/resume", response_model=Dict)
async def resume_game(run_id: str, user: User = Depends(get_current_user)):
    """Resume a build. If a live control exists (a paused in-flight build), resume it in place. If
    not (the build thread died — container restart, redeploy), RE-ENQUEUE the build from durable
    on-disk state so it rebuilds where it left off. The run is already `charged`, so re-enqueue
    never re-charges."""
    from maestro.run_control import get as get_control

    state = _require_state(run_id, user)
    ctrl = get_control(run_id)
    if ctrl is not None:
        ctrl.request_resume()
        return {"run_id": run_id, "status": "running"}
    if not state.is_charged():
        raise HTTPException(status_code=409, detail="no build to resume for this run")
    try:
        position = build_queue.enqueue(run_id, user.id)
    except AlreadyQueued:
        raise HTTPException(status_code=409, detail="build already in progress")
    return {"status": "building" if position == 0 else "queued",
            "run_id": run_id, "queue_position": position}


@router.post("/{run_id}/auto-pause", response_model=Dict)
async def auto_pause_game(run_id: str, body: AutoPauseBody, user: User = Depends(get_current_user)):
    """Arm/disarm auto-pause: when armed, the build parks itself each time a system finishes."""
    _require_state(run_id, user)
    _control(run_id).set_auto_pause(body.enabled)
    return {"run_id": run_id, "auto_pause": body.enabled}


@router.post("/{run_id}/fix", response_model=Dict)
async def fix_game(run_id: str, body: FixBody, user: User = Depends(get_current_user)):
    """Patch a built game from a free-text note ('the player falls through the floor'), on a
    background thread. Progress + completion stream over the websocket (build_*)."""
    _require_state(run_id, user)
    from maestro.codegen.run import fix_from_note

    key = f"fix:{run_id}"
    with _active_lock:
        if key in _active:
            raise HTTPException(status_code=409, detail="a fix is already running for this run")
        _active.add(key)

    def _run():
        try:
            fix_from_note(run_id, body.note)
        except Exception:
            logger.exception("fix failed for %s", run_id)
        finally:
            with _active_lock:
                _active.discard(key)

    threading.Thread(target=_run, daemon=True, name=f"fix-{run_id}").start()
    return {"status": "fixing", "run_id": run_id}


@router.post("/{run_id}/assets", response_model=Dict)
async def skin_assets(run_id: str, user: User = Depends(get_current_user)):
    """Skin the built game's placeholder shapes with generated sprites/meshes, on a background
    thread. Emits assets_started / assets_done over the websocket."""
    _require_state(run_id, user)
    from maestro.codegen.reskin import add_assets
    from tools.build_events import _emit

    key = f"assets:{run_id}"
    with _active_lock:
        if key in _active:
            raise HTTPException(status_code=409, detail="assets are already being skinned for this run")
        _active.add(key)

    def _run():
        _emit("assets_started", run_id)
        try:
            result = add_assets(run_id)
            _emit("assets_done", run_id, ok=result.get("ok", False),
                  mode=result.get("mode"), rendered=result.get("generated", []))
        except Exception:
            logger.exception("asset skin failed for %s", run_id)
            _emit("assets_done", run_id, ok=False, mode=None, rendered=[])
        finally:
            with _active_lock:
                _active.discard(key)

    threading.Thread(target=_run, daemon=True, name=f"assets-{run_id}").start()
    return {"status": "skinning", "run_id": run_id}
