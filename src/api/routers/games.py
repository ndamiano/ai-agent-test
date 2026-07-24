"""Games router — browse and drive codegen build runs.

A "game" is a games row in the platform db plus its run dir under
<working_directory>/runs/<run_id>/. The list reads the db only; the detail view reads the spec
from disk (source of truth) and derives live status from the build queue + the run control.
Freeze, build (queued on the single GPU), pause/resume, fix-from-note, and asset skinning all
live here.
"""

import asyncio
import json
import logging
import re
import threading
from typing import Dict, List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from auth import store
from auth.billing import SECONDS_PER_CREDIT, cost
from auth.deps import get_current_user
from auth.store import User
from db import store as db_store
from db.estimates import cheapest_seconds
from maestro.codegen import build_chain
from maestro.codegen.gates import RUNTIME_DIR, game_dir
from maestro.codegen.reskin import AlreadySkinning, add_assets, regenerate_asset
from maestro.codegen.run import freeze_spec
from maestro.run_control import get as get_control
from maestro.state import RunState
from tools.build_events import _emit

logger = logging.getLogger(__name__)
router = APIRouter()


class BuildBody(BaseModel):
    auto_pause: bool = False


class AutoPauseBody(BaseModel):
    enabled: bool


class FixBody(BaseModel):
    note: str = ""


class RegenerateBody(BaseModel):
    prompt: str
    mode: Literal["full", "img2img"] = "full"


# Asset skins in flight (one per run) — they run on the image/mesh queues, not the build GPU, so
# they don't ride the build queue. A daemon thread runs each; the set gates re-entry.
_active_lock = threading.Lock()
_active: set = set()


def _built(run_id: str) -> bool:
    return (RUNTIME_DIR / "games" / run_id / "main.js").exists()


def _require_state(run_id: str, user: User):
    """The run's state, scoped to its owner: 404 if there's no spec, 403 if it isn't this user's."""
    owner = db_store.owner_of(run_id)
    state = RunState(run_id)
    if owner is None or state.read_spec() is None:
        raise HTTPException(status_code=404, detail=f"no game {run_id!r}")
    if owner != user.id:
        raise HTTPException(status_code=403, detail="not your game")
    return state


def _require_compute(run_id: str) -> None:
    """Refuse work a game can't pay for, BEFORE it occupies the build queue. Enqueue enforces the
    same budget per job, so this is the fast, legible failure rather than the safety net: without
    it a broke run wins the GPU slot and then thrashes on refused jobs until its step cap."""
    remaining = db_store.compute_remaining(run_id)
    if remaining < cheapest_seconds():
        raise HTTPException(status_code=402, detail={
            "reason": "compute_exhausted", "run_id": run_id,
            "seconds_remaining": max(0.0, remaining)})


@router.get("", response_model=List[Dict])
async def list_games(user: User = Depends(get_current_user)):
    """Lightweight summary of the caller's games — db rows only, no per-row file reads."""
    games: List[Dict] = []
    for row in db_store.list_games(user.id):
        if not row["title"] and row["status"] == "draft":
            continue   # created but never drafted — nothing to show yet
        games.append({
            "run_id": row["id"],
            "title": row["title"],
            "mode": row["mode"],
            "status": row["status"],
            "frozen": row["status"] != "draft",
            "built": _built(row["id"]),
            "building": build_chain.is_active(row["id"]),
            "mtime": row["updated_at"],
        })
    games.sort(key=lambda g: g["mtime"], reverse=True)
    return games


@router.get("/{run_id}", response_model=Dict)
async def get_game(run_id: str, user: User = Depends(get_current_user)):
    """Full detail for one game: the freeform spec, built/building state, and live status."""
    state = _require_state(run_id, user)
    spec_data = state.read_spec()
    row = db_store.game(run_id) or {}
    ctrl = get_control(run_id)
    built = _built(run_id)
    active = build_chain.status_of(run_id)
    if active:
        live = "fixing" if active["kind"] == "fix" else "building"
        status = "paused" if ctrl and ctrl.paused else live
    else:
        status = "built" if built else "idle"
    return {
        "run_id": run_id,
        "spec": spec_data,
        "mode": spec_data.get("mode", ""),
        "frozen": bool(spec_data.get("frozen")),
        "built": built,
        "building": active is not None,
        "status": status,
        "queue_position": None,   # builds no longer queue behind each other; kept for the client shape
        "auto_pause": ctrl.auto_pause if ctrl else False,
        "assets_exist": (game_dir(state.run_dir) / "assets.json").exists(),
        "play_url": f"/play/index.html?game={run_id}" if built else None,
        "credits_spent": row.get("credits_spent", 0),
        # Compute budget as a fraction remaining (0..1), never raw seconds — seconds_used is
        # deliberately not surfaced (it would expose actual GPU spend). None ⇒ uncharged, no bar.
        "budget_pct_remaining": _budget_pct(row, run_id),
    }


def _budget_pct(row: Dict, run_id: str) -> Optional[float]:
    granted = row.get("seconds_granted", 0)
    if granted <= 0:
        return None
    return max(0.0, min(1.0, db_store.compute_remaining(run_id) / granted))


_ASSET_ID = re.compile(r"^[a-zA-Z0-9_-]+$")
# (kind, manifest key, file extension, media type)
_ASSET_KINDS = (
    ("sprite", "sprites", "png", "image/png"),
    ("mesh", "meshes", "glb", "model/gltf-binary"),
)


@router.get("/{run_id}/assets", response_model=List[Dict])
async def game_assets(run_id: str, user: User = Depends(get_current_user)):
    """The built game's asset manifest with per-asset render status. Empty until the game is
    skinned. `status`: ready (the file is on disk), rendering (a skin batch is in flight), or
    pending (planned but not yet rendered). The bytes come from the sibling blob route, so the
    frontend never touches the public /play mount."""
    state = _require_state(run_id, user)
    manifest_path = game_dir(state.run_dir) / "assets.json"
    if not manifest_path.exists():
        return []
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assets_dir = game_dir(state.run_dir) / "assets"
    rendering = db_store.has_active_batch(run_id)
    out: List[Dict] = []
    for kind, key, ext, _media in _ASSET_KINDS:
        for entry in manifest.get(key, []):
            aid = entry["id"]
            ready = (assets_dir / f"{aid}.{ext}").exists()
            out.append({
                "id": aid, "kind": kind,
                "status": "ready" if ready else ("rendering" if rendering else "pending"),
                "w": entry.get("w"), "h": entry.get("h"),
            })
    return out


@router.get("/{run_id}/assets/{asset_id}")
async def game_asset_blob(run_id: str, asset_id: str, user: User = Depends(get_current_user)):
    """Stream one rendered asset (png or glb), authed + ownership-checked. Replaces the public
    /play static path for the management UI."""
    state = _require_state(run_id, user)
    if not _ASSET_ID.match(asset_id):
        raise HTTPException(status_code=400, detail="bad asset id")
    assets_dir = game_dir(state.run_dir) / "assets"
    for _kind, _key, ext, media in _ASSET_KINDS:
        path = assets_dir / f"{asset_id}.{ext}"
        if path.exists():
            return FileResponse(path, media_type=media)
    raise HTTPException(status_code=404, detail="no such asset")


@router.post("/{run_id}/assets/{asset_id}/regenerate", response_model=Dict)
async def regenerate_game_asset(run_id: str, asset_id: str, body: RegenerateBody,
                                user: User = Depends(get_current_user)):
    """Re-render ONE asset of a built game with a new prompt, without re-skinning the whole game.
    Enqueues a single image job that saves the new png/glb and re-stages it through the same `skin`
    finalize a full re-skin uses — so assets_done fires and the gallery refetches. A build row of
    kind 'assets' tracks it (the finalize closes it out)."""
    _require_state(run_id, user)
    if not _ASSET_ID.match(asset_id):
        raise HTTPException(status_code=400, detail="bad asset id")
    prompt = body.prompt.strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="a prompt is required")
    _require_compute(run_id)
    build_id = db_store.create_build(run_id, kind="assets")
    db_store.build_started(build_id)
    batch_id = await asyncio.to_thread(regenerate_asset, run_id, asset_id, prompt, body.mode,
                                       build_id)
    if batch_id is None:
        db_store.build_finished(build_id, "failed")
        raise HTTPException(status_code=400, detail="prompt blocked by the safety filter")
    return {"status": "regenerating", "run_id": run_id, "asset_id": asset_id}


@router.get("/{run_id}/events", response_model=List[Dict])
async def game_events(run_id: str, after: int = 0, user: User = Depends(get_current_user)):
    """The game's durable event log (spec/build lifecycle), for catch-up after a reconnect —
    the websocket only delivers what happens while a socket is open."""
    _require_state(run_id, user)
    return db_store.events_for(run_id, after_id=after)


@router.post("/{run_id}/freeze", response_model=Dict)
async def freeze_game(run_id: str, user: User = Depends(get_current_user)):
    """Human approval action — freeze the spec so the build can run."""
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

    if build_chain.is_active(run_id):
        raise HTTPException(status_code=409, detail="build already in progress")
    if not db_store.is_charged(run_id):
        price = cost(spec_data)
        if not store.deduct(user.id, price, "build", run_id):
            raise HTTPException(status_code=402, detail={
                "reason": "insufficient_credits", "balance": store.balance(user.id), "cost": price})
        db_store.charge_game(run_id, price, price * SECONDS_PER_CREDIT)
    _require_compute(run_id)

    # kickoff seeds the scaffolds, runs the first gate sweep and enqueues the first llm turn — that
    # touches disk + tsc, so off the event loop. It returns as soon as the turn is queued.
    await asyncio.to_thread(build_chain.kickoff, run_id, kind="build", auto_pause=body.auto_pause)
    return {"status": "building", "run_id": run_id, "queue_position": 0}


def _control(run_id: str):
    """The live control for an in-flight build, or 409 if nothing is building."""
    ctrl = get_control(run_id)
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
    _require_state(run_id, user)
    _require_compute(run_id)
    if build_chain.is_active(run_id):
        # A mid-flight build — paused, or one whose driver died (cursor on disk). Re-drive it.
        await asyncio.to_thread(build_chain.resume, run_id)
        return {"run_id": run_id, "status": "running"}
    if not db_store.is_charged(run_id):
        raise HTTPException(status_code=409, detail="no build to resume for this run")
    # A finished/failed build: re-run the gate loop over the on-disk game (rebuild where it left off).
    await asyncio.to_thread(build_chain.kickoff, run_id, kind="build")
    return {"status": "building", "run_id": run_id, "queue_position": 0}


@router.post("/{run_id}/auto-pause", response_model=Dict)
async def auto_pause_game(run_id: str, body: AutoPauseBody, user: User = Depends(get_current_user)):
    """Arm/disarm auto-pause: when armed, the build parks itself each time a system finishes."""
    _require_state(run_id, user)
    _control(run_id).set_auto_pause(body.enabled)
    return {"run_id": run_id, "auto_pause": body.enabled}


@router.post("/{run_id}/fix", response_model=Dict)
async def fix_game(run_id: str, body: FixBody, user: User = Depends(get_current_user)):
    """Patch a built game from a free-text note ('the player falls through the floor'). Queued on
    the same single-GPU queue as a build — a fix ends in its own re-gating build loop, so the two
    can't run at once. Progress + completion stream over the websocket (fix_started, build_*)."""
    _require_state(run_id, user)
    _require_compute(run_id)
    if build_chain.is_active(run_id):
        raise HTTPException(status_code=409, detail="a build or fix is already running for this run")
    await asyncio.to_thread(build_chain.kickoff, run_id, kind="fix", note=body.note)
    return {"status": "fixing", "run_id": run_id, "queue_position": 0}


@router.post("/{run_id}/assets", response_model=Dict)
async def skin_assets(run_id: str, user: User = Depends(get_current_user)):
    """Skin the built game's placeholder shapes with generated sprites/meshes, on a background
    thread. Emits assets_started / assets_done over the websocket."""
    _require_state(run_id, user)
    _require_compute(run_id)
    key = f"assets:{run_id}"
    # Two guards, because the stage now outlives its thread: the key covers the plan-and-enqueue
    # half, the batch query covers the queued half. Without the second, a double-click enqueues a
    # second full set of image/mesh jobs and pays for them.
    if db_store.has_active_batch(run_id):
        raise HTTPException(status_code=409, detail="assets are already being skinned for this run")
    with _active_lock:
        if key in _active:
            raise HTTPException(status_code=409, detail="assets are already being skinned for this run")
        _active.add(key)

    def _run(build_id: str):
        db_store.build_started(build_id)
        _emit("assets_started", run_id)
        try:
            # Returns once the asset jobs are ENQUEUED. assets_done and build_finished are the
            # batch finalize's job, since the render outlives this thread by minutes.
            add_assets(run_id, build_id=build_id)
        except AlreadySkinning:
            # Lost the race to the build's own auto-skin — that skin owns the endgame.
            db_store.build_finished(build_id, "failed")
        except Exception:
            logger.exception("asset skin failed for %s", run_id)
            _emit("assets_done", run_id, ok=False, mode=None, rendered=[])
            db_store.build_finished(build_id, "failed")
        finally:
            with _active_lock:
                _active.discard(key)

    # Anything that throws before the thread owns the key has to hand it back, or every later
    # skin of this run 409s until the process restarts.
    try:
        build_id = db_store.create_build(run_id, kind="assets")
        threading.Thread(target=_run, args=(build_id,), daemon=True, name=f"assets-{run_id}").start()
    except Exception:
        with _active_lock:
            _active.discard(key)
        raise
    return {"status": "skinning", "run_id": run_id}
