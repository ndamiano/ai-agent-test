"""Games router — browse and drive codegen build runs.

A "game" is a games row in the platform db plus its run dir under
<working_directory>/runs/<run_id>/. The list reads the db only; the detail view reads the prompt
from disk (source of truth) and derives live status from the durable build cursor.
Build (which also stores the prompt it was given), pause/resume/stop, fix-from-note, and asset
skinning all live here.
"""

import asyncio
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
from maestro.codegen.assets import AlreadyRendering, add_assets, read_manifest, regenerate_asset
from maestro.codegen.staging import game_dir, is_staged
from maestro.codegen.run import create_run, set_prompt
from tools.safety import log_violation, screen_text
from maestro.state import RunState
from tools.build_events import _emit

logger = logging.getLogger(__name__)
router = APIRouter()


class NewGameBody(BaseModel):
    prompt: str


class BuildBody(BaseModel):
    prompt: Optional[str] = None


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
    return is_staged(run_id)


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
            continue   # created but has no prompt yet — nothing to show
        games.append({
            "run_id": row["id"],
            "title": row["title"],
            "status": row["status"],
            "built": _built(row["id"]),
            "building": build_chain.is_active(row["id"]),
            "mtime": row["updated_at"],
        })
    games.sort(key=lambda g: g["mtime"], reverse=True)
    return games


@router.post("", response_model=Dict)
async def create_game(body: NewGameBody, user: User = Depends(get_current_user)):
    """Make a new game: the prompt the user wrote becomes the run, and the build starts. Nothing
    exists server-side until this call, so an abandoned box leaves nothing behind."""
    text = body.prompt.strip()
    if not text:
        raise HTTPException(status_code=400, detail="the prompt is empty")
    violation = screen_text(text)
    if violation is not None:
        log_violation(violation, user_id=user.id, source="new_game")
        raise HTTPException(status_code=400, detail="this prompt can't be built")

    price = cost({"request": text})
    if store.balance(user.id) < price:
        raise HTTPException(status_code=402, detail={
            "reason": "insufficient_credits", "balance": store.balance(user.id), "cost": price})

    run_id = await asyncio.to_thread(create_run, user.id)
    await asyncio.to_thread(set_prompt, run_id, text, event="prompt_proposed")
    if not store.deduct(user.id, price, "build", run_id):
        raise HTTPException(status_code=402, detail={
            "reason": "insufficient_credits", "balance": store.balance(user.id), "cost": price})
    db_store.charge_game(run_id, price, price * SECONDS_PER_CREDIT)
    _require_compute(run_id)
    await asyncio.to_thread(build_chain.kickoff, run_id, kind="build")
    return {"run_id": run_id, "status": "building"}


@router.get("/{run_id}", response_model=Dict)
async def get_game(run_id: str, user: User = Depends(get_current_user)):
    """Full detail for one game: the build prompt, built/building state, and live status."""
    state = _require_state(run_id, user)
    spec_data = state.read_spec()
    row = db_store.game(run_id) or {}
    built = _built(run_id)
    active = build_chain.status_of(run_id)
    if active:
        live = "fixing" if active["kind"] == "fix" else "building"
        status = "paused" if active["paused"] else live
    else:
        status = "built" if built else "idle"
    return {
        "run_id": run_id,
        "prompt": spec_data.get("request", ""),
        "title": spec_data.get("title", ""),
        "built": built,
        "building": active is not None,
        "status": status,
        "queue_position": None,   # builds no longer queue behind each other; kept for the client shape
        "assets_exist": (game_dir(state.run_dir) / "assets.json").exists(),
        "play_url": f"/play/games/{run_id}/index.html" if built else None,
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
_MEDIA = {"png": "image/png", "glb": "model/gltf-binary"}


def _ext(entry: Dict) -> str:
    return "glb" if entry.get("kind") == "mesh" else "png"


@router.get("/{run_id}/assets", response_model=List[Dict])
async def game_assets(run_id: str, user: User = Depends(get_current_user)):
    """The game's own asset manifest with per-asset render status. Empty until the game declares
    one. `status`: ready (the file is on disk), rendering (a batch is in flight), or pending. The
    bytes come from the sibling blob route, so the frontend never touches the public /play mount."""
    state = _require_state(run_id, user)
    assets_dir = game_dir(state.run_dir) / "assets"
    rendering = db_store.has_active_batch(run_id)
    out: List[Dict] = []
    for entry in read_manifest(state.run_dir):
        aid = entry["id"]
        ready = (assets_dir / f"{aid}.{_ext(entry)}").exists()
        out.append({"id": aid, "kind": entry.get("kind") or "image",
                    "status": "ready" if ready else ("rendering" if rendering else "pending"),
                    "prompt": entry["prompt"]})
    return out


@router.get("/{run_id}/assets/{asset_id}")
async def game_asset_blob(run_id: str, asset_id: str, user: User = Depends(get_current_user)):
    """Stream one rendered asset (png or glb), authed + ownership-checked. Replaces the public
    /play static path for the management UI."""
    state = _require_state(run_id, user)
    if not _ASSET_ID.match(asset_id):
        raise HTTPException(status_code=400, detail="bad asset id")
    assets_dir = game_dir(state.run_dir) / "assets"
    for ext, media in _MEDIA.items():
        path = assets_dir / f"{asset_id}.{ext}"
        if path.exists():
            return FileResponse(path, media_type=media)
    raise HTTPException(status_code=404, detail="no such asset")


@router.post("/{run_id}/assets/{asset_id}/regenerate", response_model=Dict)
async def regenerate_game_asset(run_id: str, asset_id: str, body: RegenerateBody,
                                user: User = Depends(get_current_user)):
    """Re-render ONE asset with a change note, without re-rendering the whole game. Enqueues a
    single image job through the same finalize a full render uses, so assets_done fires and the
    gallery refetches."""
    _require_state(run_id, user)
    if not _ASSET_ID.match(asset_id):
        raise HTTPException(status_code=400, detail="bad asset id")
    note = body.prompt.strip()
    if not note:
        raise HTTPException(status_code=400, detail="a prompt is required")
    _require_compute(run_id)
    out = await asyncio.to_thread(regenerate_asset, run_id, asset_id, note, body.mode)
    if not out["ok"]:
        raise HTTPException(status_code=400, detail=out["error"])
    return {"status": "regenerating", "run_id": run_id, "asset_id": asset_id}


@router.get("/{run_id}/events", response_model=List[Dict])
async def game_events(run_id: str, after: int = 0, user: User = Depends(get_current_user)):
    """The game's durable event log (spec/build lifecycle), for catch-up after a reconnect —
    the websocket only delivers what happens while a socket is open."""
    _require_state(run_id, user)
    return db_store.events_for(run_id, after_id=after)


@router.post("/{run_id}/build", response_model=Dict)
async def build_game(run_id: str, body: BuildBody = BuildBody(),
                     user: User = Depends(get_current_user)):
    """Queue a build on the single GPU. It runs immediately if the worker is free, else it waits
    with a `queue_position`. Progress streams over the websocket.

    `prompt` carries the user's edit of the text: pressing Build IS approving what is in the box,
    so the build is the only thing that writes it. The driver reads it from disk, not from this
    request — a build outlives the process that started it.

    A run is charged ONCE, gated on a durable `charged` flag: the first enqueue deducts
    `cost(spec)`; every later enqueue for the same run (a re-trigger, a resume after a dead build)
    finds it already flagged and never re-charges. Charged stays charged — there is no automatic
    refund."""
    state = _require_state(run_id, user)
    if build_chain.is_active(run_id):
        raise HTTPException(status_code=409, detail="build already in progress")
    if body.prompt is not None:
        violation = screen_text(body.prompt)
        if violation is not None:
            log_violation(violation, user_id=user.id, source="prompt_edit")
            raise HTTPException(status_code=400, detail="this prompt can't be built")
        try:
            await asyncio.to_thread(set_prompt, run_id, body.prompt)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
    spec_data = state.read_spec()
    if not db_store.is_charged(run_id):
        price = cost(spec_data)
        if not store.deduct(user.id, price, "build", run_id):
            raise HTTPException(status_code=402, detail={
                "reason": "insufficient_credits", "balance": store.balance(user.id), "cost": price})
        db_store.charge_game(run_id, price, price * SECONDS_PER_CREDIT)
    _require_compute(run_id)

    # kickoff seeds the scaffolds, runs the first gate sweep and enqueues the first llm turn — that
    # touches disk + tsc, so off the event loop. It returns as soon as the turn is queued.
    await asyncio.to_thread(build_chain.kickoff, run_id, kind="build")
    return {"status": "building", "run_id": run_id, "queue_position": 0}


@router.post("/{run_id}/pause", response_model=Dict)
async def pause_game(run_id: str, user: User = Depends(get_current_user)):
    """Pause a running build — it halts at the next step boundary (state stays consistent)."""
    _require_state(run_id, user)
    if not build_chain.pause(run_id):
        raise HTTPException(status_code=409, detail="no build in progress for this run")
    return {"run_id": run_id, "status": "pausing"}


@router.post("/{run_id}/stop", response_model=Dict)
async def stop_game(run_id: str, user: User = Depends(get_current_user)):
    """Stop a build for good, keeping whatever it has written. A run that never wrote an index.html
    ends `failed`; one that did is playable and ends `built`, exactly as a step-capped build does."""
    _require_state(run_id, user)
    if not await asyncio.to_thread(build_chain.stop, run_id):
        raise HTTPException(status_code=409, detail="no build in progress for this run")
    return {"run_id": run_id, "status": "stopped"}


@router.post("/{run_id}/resume", response_model=Dict)
async def resume_game(run_id: str, user: User = Depends(get_current_user)):
    """Resume a build. A mid-flight cursor (paused, or one whose driver died — container restart,
    redeploy) is re-driven in place from durable on-disk state, so it rebuilds where it left off.
    The run is already `charged`, so re-enqueue never re-charges."""
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
    """Render the art the game declared in assets.json, on a background thread. Emits
    assets_started / assets_done over the websocket."""
    _require_state(run_id, user)
    _require_compute(run_id)
    key = f"assets:{run_id}"
    # Two guards, because the stage now outlives its thread: the key covers the plan-and-enqueue
    # half, the batch query covers the queued half. Without the second, a double-click enqueues a
    # second full set of image/mesh jobs and pays for them.
    if db_store.has_active_batch(run_id):
        raise HTTPException(status_code=409, detail="assets are already rendering for this run")
    with _active_lock:
        if key in _active:
            raise HTTPException(status_code=409, detail="assets are already rendering for this run")
        _active.add(key)

    def _run(build_id: str):
        db_store.build_started(build_id)
        _emit("assets_started", run_id)
        try:
            # Returns once the asset jobs are ENQUEUED. assets_done and build_finished are the
            # batch finalize's job, since the render outlives this thread by minutes.
            add_assets(run_id, build_id=build_id)
        except AlreadyRendering:
            # Lost the race to the build's own asset lane — that batch owns the endgame.
            db_store.build_finished(build_id, "failed")
        except Exception:
            logger.exception("asset render failed for %s", run_id)
            _emit("assets_done", run_id, ok=False, rendered=[])
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
