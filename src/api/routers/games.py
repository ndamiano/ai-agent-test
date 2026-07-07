"""Games router — read-only browse of build runs.

A "game" is one run dir under <working_directory>/runs/<run_id>/. The list is
intentionally cheap (spec read only — no validate, which would trigger a Ren'Py
compile per row). The detail view runs validate once on demand to surface the to-do.
Build orchestration (freeze/build/streaming) lives in the next phase.
"""

import asyncio
import logging
import mimetypes
import threading
from typing import Dict, List

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from api.build_queue import build_queue, AlreadyQueued
from auth.deps import get_current_user
from auth.store import User

logger = logging.getLogger(__name__)
router = APIRouter()


class TodoBody(BaseModel):
    component_id: str
    text: str


class ResolveBody(BaseModel):
    done: bool = True


class WaiveBody(BaseModel):
    idkey: str
    note: str = ""


class UnwaiveBody(BaseModel):
    idkey: str


class ComponentBody(BaseModel):
    content: Dict


class NodeEditBody(BaseModel):
    line_index: int = None
    text: str = None
    speaker: str = None
    emotion: str = None
    effects: List = None
    end: Dict = None
    content: Dict = None


class CompileBody(BaseModel):
    distribute: bool = False


class BuildBody(BaseModel):
    auto_pause: bool = False


class AmendBody(BaseModel):
    changes: Dict
    reason: str = "human edited the plan"


class AutoPauseBody(BaseModel):
    enabled: bool


class RewriteBody(BaseModel):
    note: str = ""


class DirtyBody(BaseModel):
    idkey: str
    note: str = ""


class ThumbBody(BaseModel):
    idkey: str


class AssetRegenBody(BaseModel):
    filename: str

# Node rewrites in flight (one per node) guard lock. Builds are serialized by the build queue
# (api.build_queue); rewrites run on their own threads and only need double-fire protection.
_active_lock = threading.Lock()


def _runs_dir():
    from tools.execution_context import resolve_base_path
    return resolve_base_path() / "runs"


def _is_built(run_dir) -> bool:
    return (run_dir / "game_output").exists()


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
    """Lightweight summary of the caller's runs. No validate (avoids per-row compiles)."""
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
            "frozen": bool(spec.get("frozen")),
            "built": _is_built(run_dir),
            "building": build_queue.is_active(run_dir.name),
            "n_components": len(state.component_ids()),
            "mtime": run_dir.stat().st_mtime,
        })

    games.sort(key=lambda g: g["mtime"], reverse=True)
    return games


@router.get("/{run_id}", response_model=Dict)
async def get_game(run_id: str, user: User = Depends(get_current_user)):
    """Full detail for one game: spec, built artifact, and the current to-do."""
    from maestro.spec import Spec
    from maestro.modules.human import effective_failures
    from maestro.run_control import get as get_control

    state = _require_state(run_id, user)
    spec_data = state.read_spec()
    spec = Spec(spec_data)
    ctrl = get_control(run_id)
    built = _is_built(state.run_dir)
    images = state.run_dir / "game_output" / "game" / "images"
    qstate = build_queue.state_of(run_id)
    if qstate and qstate["status"] == "queued":
        status, queue_position = "queued", qstate["position"]
    elif qstate:   # building now — the control carries the live running/paused status
        status, queue_position = (ctrl.status if ctrl else "running"), None
    else:
        status, queue_position = (ctrl.status if ctrl else ("built" if built else "idle")), None
    return {
        "run_id": run_id,
        "spec": spec_data,
        "artifact": state.load_artifact(),
        "todo": effective_failures(spec_data, state),
        "human_todos": state.read_human_todos(),
        "waivers": state.read_waivers(),
        "frozen": spec.frozen,
        "built": built,
        "building": qstate is not None,
        "status": status,
        "queue_position": queue_position,
        "auto_pause": ctrl.auto_pause if ctrl else False,
        "assets_exist": images.is_dir() and any(images.glob("*.png")),
    }


@router.patch("/{run_id}/spec", response_model=Dict)
async def amend_game_spec_route(run_id: str, body: AmendBody, user: User = Depends(get_current_user)):
    """Edit a draft's plan before freeze — change modules / sizing / title. Un-freezes + re-resolves
    (foundation forced, deps expanded, engine re-derived). Returns the fresh detail."""
    from tools.spec_tools import amend_spec

    _require_state(run_id, user)
    await asyncio.to_thread(amend_spec, run_id, body.changes, body.reason)
    return await get_game(run_id, user)


@router.post("/{run_id}/freeze", response_model=Dict)
async def freeze_game(run_id: str, user: User = Depends(get_current_user)):
    """Human approval action — freeze the spec so the build can run."""
    from tools.spec_tools import freeze_spec

    _require_state(run_id, user)
    return await asyncio.to_thread(freeze_spec, run_id)


@router.post("/{run_id}/build", response_model=Dict)
async def build_game(run_id: str, body: BuildBody = BuildBody(),
                     user: User = Depends(get_current_user)):
    """Queue a build on the single GPU. It runs immediately if the worker is free, else it waits
    with a `queue_position`. Progress streams over the websocket."""
    spec_data = _require_state(run_id, user).read_spec()
    if not spec_data.get("frozen"):
        raise HTTPException(status_code=400, detail="freeze the spec before building")

    from auth import store
    from auth.billing import cost

    price = cost(spec_data)
    if not store.deduct(user.id, price, "build", run_id):
        raise HTTPException(status_code=402, detail={
            "reason": "insufficient_credits", "balance": store.balance(user.id), "cost": price})

    try:
        position = build_queue.enqueue(run_id, user.id, body.auto_pause, cost=price)
    except AlreadyQueued:
        store.refund(user.id, price, "build_not_started", run_id)
        raise HTTPException(status_code=409, detail="build already in progress")
    except Exception:
        store.refund(user.id, price, "build_not_started", run_id)
        raise

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
    """Resume a paused build."""
    _require_state(run_id, user)
    _control(run_id).request_resume()
    return {"run_id": run_id, "status": "running"}


@router.post("/{run_id}/cancel", response_model=Dict)
async def cancel_game(run_id: str, user: User = Depends(get_current_user)):
    """Cancel a running build — it unwinds at the next step boundary."""
    _require_state(run_id, user)
    _control(run_id).request_cancel()
    return {"run_id": run_id, "status": "cancelling"}


@router.post("/{run_id}/auto-pause", response_model=Dict)
async def auto_pause_game(run_id: str, body: AutoPauseBody, user: User = Depends(get_current_user)):
    """Arm/disarm auto-pause: when armed, the build parks itself each time a component finishes."""
    _require_state(run_id, user)
    _control(run_id).set_auto_pause(body.enabled)
    return {"run_id": run_id, "auto_pause": body.enabled}


@router.post("/{run_id}/todos", response_model=Dict)
async def add_todo_game(run_id: str, body: TodoBody, user: User = Depends(get_current_user)):
    """Add a human todo against a component — the build won't complete while it's open.
    A live build parks in `awaiting_human` once its machine checks pass."""
    from maestro.modules.human import add_todo

    return add_todo(_require_state(run_id, user), body.component_id, body.text)


@router.patch("/{run_id}/todos/{todo_id}", response_model=Dict)
async def resolve_todo_game(run_id: str, todo_id: str, body: ResolveBody,
                           user: User = Depends(get_current_user)):
    """Mark a human todo done (or reopen it) — only the human arbitrates this."""
    from maestro.modules.human import resolve_todo

    if not resolve_todo(_require_state(run_id, user), todo_id, body.done):
        raise HTTPException(status_code=404, detail=f"no todo {todo_id!r}")
    return {"run_id": run_id, "todo_id": todo_id, "done": body.done}


@router.post("/{run_id}/waive", response_model=Dict)
async def waive_game(run_id: str, body: WaiveBody, user: User = Depends(get_current_user)):
    """Accept a machine check still reported red — it leaves the to-do and no longer blocks
    completion. Keyed on the error's `idkey` (from the detail endpoint's todo list)."""
    from maestro.modules.human import waive

    return waive(_require_state(run_id, user), body.idkey, body.note)


@router.post("/{run_id}/unwaive", response_model=Dict)
async def unwaive_game(run_id: str, body: UnwaiveBody, user: User = Depends(get_current_user)):
    """Reinstate a previously waived check."""
    from maestro.modules.human import unwaive

    if not unwaive(_require_state(run_id, user), body.idkey):
        raise HTTPException(status_code=404, detail=f"no waiver {body.idkey!r}")
    return {"run_id": run_id, "idkey": body.idkey}


def _require_editable(run_id: str):
    """Regenerating art / compiling / rewriting a scene all race the executor thread on the same
    files, so these still only run when no build is in flight OR the build is parked (paused /
    awaiting_human). A plain content EDIT is exempt (see the asset-edit endpoints below) — it's
    allowed at any build state and reflags its downstream closure instead of blocking."""
    from maestro.run_control import get as get_control

    if build_queue.is_active(run_id):
        ctrl = get_control(run_id)
        if ctrl is None or ctrl.status not in ("paused", "awaiting_human"):
            raise HTTPException(status_code=409,
                                detail="pause the build before editing or regenerating")


def _human_tools(spec, state):
    """build_tools wired with the composed modules, so a human edit is still schema-validated."""
    from maestro.tools import build_tools
    from maestro.modules import compose

    spec_data = getattr(spec, "data", spec)
    return build_tools(spec_data, state, compose(spec_data.get("modules", [])))


def _spec_state(run_id: str, user: User):
    from maestro.spec import Spec

    state = _require_state(run_id, user)
    return Spec(state.read_spec()), state


@router.put("/{run_id}/component/{component_id}", response_model=Dict)
async def edit_component_game(run_id: str, component_id: str, body: ComponentBody,
                             user: User = Depends(get_current_user)):
    """Human edit of a whole component (schema-validated; overrides the lock). UN-GATED (Epic C3):
    allowed at any build state — a hand-edit is itself a rewrite, so it clears each item's own
    dirty flag and reflags its downstream closure instead of waiting for a pause."""
    spec, state = _spec_state(run_id, user)
    result = _human_tools(spec, state)["write_component"](component_id, body.content, force=True)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error", "invalid component"))
    new_content = state.read_component(component_id) or {}
    item_ids = [iid for iid, _ in _component_items(component_id, new_content)]
    result.update(_after_edit(run_id, spec.data, state, component_id, item_ids))
    return result


@router.put("/{run_id}/node/{node_id}", response_model=Dict)
async def edit_node_game(run_id: str, node_id: str, body: NodeEditBody,
                        user: User = Depends(get_current_user)):
    """Human patch of one node field (e.g. fix a character's line); overrides the lock. UN-GATED
    (Epic C3): allowed at any build state."""
    spec, state = _spec_state(run_id, user)
    patch = body.model_dump(exclude_unset=True)
    result = _human_tools(spec, state)["edit_node"](node_id, force=True, **patch)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error", "invalid edit"))
    result.update(_after_edit(run_id, spec.data, state, "nodes", [node_id]))
    return result


@router.post("/{run_id}/regenerate-assets", response_model=Dict)
async def regenerate_assets_game(run_id: str, user: User = Depends(get_current_user)):
    """Regenerate the art (ComfyUI when up, placeholder fallback) from the current manifest.
    Recompile afterward to repackage the project with the new images."""
    _require_state(run_id, user)
    _require_editable(run_id)
    from renpy.fns import generate_images

    _, state = _spec_state(run_id, user)
    presentation = (state.read_spec() or {}).get("presentation", "2d")
    return generate_images(state.load_artifact(), state.run_dir,
                           presentation=presentation) or {"ok": True}


@router.post("/{run_id}/regenerate-asset", response_model=Dict)
async def regenerate_asset_game(run_id: str, body: AssetRegenBody,
                               user: User = Depends(get_current_user)):
    """Regenerate exactly ONE declared/derived image file (never the whole manifest) — the
    per-asset browser's 'try again'. Passes the run's `presentation` so an hd2d feature's mesh
    rides along with its sprite, same as the all-assets regenerate."""
    _require_state(run_id, user)
    _require_editable(run_id)
    from renpy.fns import generate_single_asset

    _, state = _spec_state(run_id, user)
    presentation = (state.read_spec() or {}).get("presentation", "2d")
    result = generate_single_asset(state.load_artifact(), state.run_dir, body.filename,
                                   presentation=presentation)
    if result.get("status") == "error":
        raise HTTPException(status_code=400, detail=result.get("error", "regeneration failed"))
    return result


def _images_dir(run_id: str, user: User):
    return _require_state(run_id, user).run_dir / "game_output" / "game" / "images"


@router.get("/{run_id}/asset-file/{filename}")
async def asset_file_game(run_id: str, filename: str, user: User = Depends(get_current_user)):
    """Stream one generated asset file's bytes (image or mesh) — the component browser's `<img
    src>` target. Guarded to the run's own images dir: `filename` is a single path segment (FastAPI
    won't match a `/` into it) and the resolved path must still land inside that directory, so a
    `..` traversal 403s instead of reaching outside the run."""
    images_dir = _images_dir(run_id, user).resolve()
    resolved = (images_dir / filename).resolve()
    if not resolved.is_relative_to(images_dir):
        raise HTTPException(status_code=403, detail="invalid asset filename")
    if not resolved.is_file():
        raise HTTPException(status_code=404, detail=f"no asset file {filename!r}")

    media_type, _ = mimetypes.guess_type(str(resolved))
    if media_type is None and resolved.suffix == ".glb":
        media_type = "model/gltf-binary"
    return FileResponse(path=resolved, media_type=media_type or "application/octet-stream")


@router.post("/{run_id}/compile", response_model=Dict)
async def compile_game(run_id: str, body: CompileBody = CompileBody(),
                      user: User = Depends(get_current_user)):
    """Compile/package the project on demand. Output served via /api/outputs/."""
    _require_state(run_id, user)
    _require_editable(run_id)
    from maestro.engines import compile_for

    spec, state = _spec_state(run_id, user)
    return compile_for(spec.engine)(state.run_dir, distribute=body.distribute)


@router.get("/{run_id}/download")
async def download_game(run_id: str, user: User = Depends(get_current_user)):
    """Stream the SELF-CONTAINED distributable (engine bundled) so the player needs no Ren'Py or
    Godot install. The backend is network-reachable (remote box), so there is no local file
    manager to 'reveal' into — the user pulls the finished game over HTTP.

    Ren'Py's `distribute` writes per-platform archives to <run>/dist; Godot's export writes one
    zip of desktop binaries to <run>/godot_dist.zip. Package the game first to produce them."""
    import shutil

    state = _require_state(run_id, user)
    run = state.run_dir

    godot_dist = run / "godot_dist.zip"
    if godot_dist.exists():
        return FileResponse(
            path=godot_dist, filename=f"{run_id}.zip", media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="{run_id}.zip"'})

    renpy_dist = run / "dist"
    if renpy_dist.is_dir() and any(renpy_dist.iterdir()):
        zip_base = run / "download"
        shutil.make_archive(str(zip_base), "zip", renpy_dist)
        return FileResponse(
            path=zip_base.with_suffix(".zip"), filename=f"{run_id}.zip", media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="{run_id}.zip"'})

    raise HTTPException(
        status_code=409,
        detail="no self-contained build yet — package the game (distribute) first")


# Node rewrites in flight (one per node), so the UI can disable a node's button while it runs.
_rewriting: set = set()


@router.post("/{run_id}/node/{node_id}/rewrite", response_model=Dict)
async def rewrite_node_game(run_id: str, node_id: str, body: RewriteBody,
                           user: User = Depends(get_current_user)):
    """Regenerate one scene from a human note ('make it tenser'), on a background thread.
    Progress + completion stream over the websocket (node_rewrite_*)."""
    _require_state(run_id, user)
    _require_editable(run_id)
    from maestro.run import rewrite_node_run

    key = f"{run_id}/{node_id}"
    with _active_lock:
        if key in _rewriting:
            raise HTTPException(status_code=409, detail="this scene is already being rewritten")
        _rewriting.add(key)

    def _run():
        try:
            rewrite_node_run(run_id, node_id, body.note)
        except Exception:
            logger.exception("rewrite failed for %s", key)
        finally:
            with _active_lock:
                _rewriting.discard(key)

    threading.Thread(target=_run, daemon=True, name=f"rewrite-{key}").start()
    return {"status": "rewriting", "run_id": run_id, "node_id": node_id}


# ── Epic C: the component-blind asset browser API ────────────────────────────────────────────
# Every component decomposes into ADDRESSABLE ITEMS at the same granularity the dirty store
# already keys on (human.asset_idkey's docstring: nodes:scene_3, characters:mara, places:
# zone_crypt, combat:firebolt). `_component_items` is that one place a new component's shape gets
# taught to the browser; everything else here (list/detail/edit/dirty/thumbs) is generic over it.
def _component_items(component_id: str, content) -> List:
    """(item_id, item_content) pairs for one component's decomposed assets. A singleton document
    with no per-item write tool (story, asset_manifest) is one asset keyed by the component id."""
    if not isinstance(content, dict):
        return [(component_id, content)]
    if component_id in ("nodes", "places"):
        inner = content.get(component_id)
        if isinstance(inner, dict):
            return list(inner.items())
    elif component_id in ("characters", "items"):
        rows = content.get(component_id)
        if isinstance(rows, list):
            return [(r["id"], r) for r in rows if isinstance(r, dict) and r.get("id")]
    elif component_id == "combat":
        out = []
        for key in ("stats", "abilities", "combatants", "encounters", "statuses"):
            out += [(r["id"], r) for r in content.get(key) or []
                   if isinstance(r, dict) and r.get("id")]
        return out
    return [(component_id, content)]


def _replace_item(component_id: str, content, item_id: str, new_item):
    """Reconstruct the whole component with `item_id` replaced by `new_item` — the inverse of
    `_component_items`, so a per-asset edit can write back through write_component. None means
    `item_id` doesn't exist in this component (a 404, not a silent append). `nodes`/`places` aren't
    handled here — they go through edit_node/write_place directly (see `_apply_asset_edit`)."""
    content = dict(content or {})
    if component_id in ("characters", "items"):
        rows = list(content.get(component_id) or [])
        idx = next((i for i, r in enumerate(rows)
                   if isinstance(r, dict) and r.get("id") == item_id), None)
        if idx is None:
            return None
        rows[idx] = {**new_item, "id": item_id}
        content[component_id] = rows
        return content
    if component_id == "combat":
        for key in ("stats", "abilities", "combatants", "encounters", "statuses"):
            rows = list(content.get(key) or [])
            idx = next((i for i, r in enumerate(rows)
                       if isinstance(r, dict) and r.get("id") == item_id), None)
            if idx is not None:
                rows[idx] = {**new_item, "id": item_id}
                content[key] = rows
                return content
        return None
    if item_id == component_id:   # singleton document (story, asset_manifest, ...)
        return new_item
    return None


def _asset_rows(component_id: str, content, dirty_by_key: Dict[str, str]) -> List[Dict]:
    from maestro.modules.human import asset_idkey

    rows = []
    for item_id, item_content in _component_items(component_id, content):
        key = asset_idkey(component_id, item_id)
        rows.append({
            "component": component_id,
            "id": item_id,
            "idkey": key,
            "content": item_content,
            "dirty": key in dirty_by_key,
            "review_note": dirty_by_key.get(key, ""),
        })
    return rows


def _apply_asset_edit(state, tools, component_id: str, item_id: str, content) -> Dict:
    """Write one asset's new content through the tool that already knows this component's shape
    (edit_node for nodes, write_place for places — both force-capable and already validated),
    falling back to a read-modify-write through write_component for everything else."""
    if component_id == "nodes":
        return tools["edit_node"](item_id, content=content, force=True)
    if component_id == "places":
        return tools["write_place"](item_id, content, force=True)
    current = state.read_component(component_id)
    if current is None:
        return {"ok": False, "error": f"no component {component_id!r}"}
    new_content = _replace_item(component_id, current, item_id, content)
    if new_content is None:
        return {"ok": False, "error": f"no asset {item_id!r} in {component_id!r}"}
    return tools["write_component"](component_id, new_content, force=True)


def _emit_asset_event(run_id: str, event_type: str, **fields) -> None:
    from tools.spec_tools import _emit
    _emit(event_type, run_id, **fields)


def _idkey_fields(asset_key: str) -> Dict:
    from maestro.modules.human import split_idkey
    component, item_id = split_idkey(asset_key)
    return {"component": component, "item_id": item_id}


def _after_edit(run_id: str, spec_data: Dict, state, component_id: str, item_ids: List[str]) -> Dict:
    """Common post-edit bookkeeping every human edit path shares (Epic B's depgraph hook + Epic C's
    live events): clear each edited item's own dirty flag (a hand-edit is itself a rewrite) and
    reflag its downstream closure, broadcasting both over the event bus so other viewers update
    live without a refetch."""
    from maestro.depgraph import mark_downstream_dirty
    from maestro.modules.human import asset_idkey, clear_dirty, dirty_entries, split_idkey

    cleared_any = False
    flagged: List[str] = []
    for item_id in item_ids:
        key = asset_idkey(component_id, item_id)
        if clear_dirty(state, key):
            cleared_any = True
        for dep in mark_downstream_dirty(state, spec_data, key):
            if dep not in flagged:
                flagged.append(dep)
        _emit_asset_event(run_id, "asset_updated", component=component_id, item_id=item_id,
                          idkey=key)
    if flagged:
        notes = {d.get("idkey"): d.get("note", "") for d in dirty_entries(state)}
        for dep in flagged:
            dep_component, dep_item = split_idkey(dep)
            _emit_asset_event(run_id, "asset_dirty_set", component=dep_component, item_id=dep_item,
                              idkey=dep, note=notes.get(dep, ""))
    return {"cleared_own_dirty": cleared_any, "flagged_dependents": flagged}


@router.get("/{run_id}/assets/{component_id}", response_model=List[Dict])
async def list_assets_game(run_id: str, component_id: str, user: User = Depends(get_current_user)):
    """Every asset in one component, uniform across every component type — the component-blind
    browser's 'list assets of type X'. Folds in the dirty store so a card needs no second fetch
    for its dirty flag / review note."""
    from maestro.modules.human import dirty_entries

    state = _require_state(run_id, user)
    content = state.read_component(component_id)
    if content is None:
        raise HTTPException(status_code=404, detail=f"no component {component_id!r}")
    dirty_by_key = {d["idkey"]: d.get("note", "") for d in dirty_entries(state)}
    rows = _asset_rows(component_id, content, dirty_by_key)
    if component_id == "asset_manifest":
        for r in rows:
            r["run_id"] = run_id
    return rows


@router.get("/{run_id}/assets/{component_id}/{item_id}", response_model=Dict)
async def get_asset_game(run_id: str, component_id: str, item_id: str,
                        user: User = Depends(get_current_user)):
    """One asset's detail — same shape as a list row."""
    from maestro.modules.human import dirty_entries

    state = _require_state(run_id, user)
    content = state.read_component(component_id)
    if content is None:
        raise HTTPException(status_code=404, detail=f"no component {component_id!r}")
    dirty_by_key = {d["idkey"]: d.get("note", "") for d in dirty_entries(state)}
    row = next((r for r in _asset_rows(component_id, content, dirty_by_key) if r["id"] == item_id),
               None)
    if row is None:
        raise HTTPException(status_code=404, detail=f"no asset {item_id!r} in {component_id!r}")
    if component_id == "asset_manifest":
        row["run_id"] = run_id
    return row


@router.put("/{run_id}/assets/{component_id}/{item_id}", response_model=Dict)
async def edit_asset_game(run_id: str, component_id: str, item_id: str, body: ComponentBody,
                         user: User = Depends(get_current_user)):
    """Uniform per-asset edit — any component, any item, at any build state (never gated on
    paused/idle; the edit reflags its downstream closure via Epic B instead of blocking)."""
    spec, state = _spec_state(run_id, user)
    tools = _human_tools(spec, state)
    result = _apply_asset_edit(state, tools, component_id, item_id, body.content)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error", "invalid edit"))
    result.update(_after_edit(run_id, spec.data, state, component_id, [item_id]))
    return result


@router.post("/{run_id}/assets/dirty", response_model=Dict)
async def set_dirty_game(run_id: str, body: DirtyBody, user: User = Depends(get_current_user)):
    """Flag one asset for attention — a manual 'look at this' (thumbs-down is the same call,
    worded differently for the UI's reject control)."""
    from maestro.modules.human import set_dirty

    state = _require_state(run_id, user)
    set_dirty(state, body.idkey, body.note)
    _emit_asset_event(run_id, "asset_dirty_set", idkey=body.idkey, note=body.note,
                      **_idkey_fields(body.idkey))
    return {"ok": True, "idkey": body.idkey, "note": body.note}


@router.post("/{run_id}/assets/thumbs-up", response_model=Dict)
async def thumbs_up_game(run_id: str, body: ThumbBody, user: User = Depends(get_current_user)):
    """Approve an asset — clears its dirty flag; the loop stops surfacing it."""
    from maestro.modules.human import clear_dirty

    state = _require_state(run_id, user)
    cleared = clear_dirty(state, body.idkey)
    if cleared:
        _emit_asset_event(run_id, "asset_dirty_cleared", idkey=body.idkey, **_idkey_fields(body.idkey))
    return {"ok": True, "idkey": body.idkey, "cleared": cleared}


@router.post("/{run_id}/assets/thumbs-down", response_model=Dict)
async def thumbs_down_game(run_id: str, body: DirtyBody, user: User = Depends(get_current_user)):
    """Reject an asset with a 'change this' note — same effect as set_dirty, worded for the UI's
    thumbs-down control."""
    from maestro.modules.human import set_dirty

    state = _require_state(run_id, user)
    set_dirty(state, body.idkey, body.note)
    _emit_asset_event(run_id, "asset_dirty_set", idkey=body.idkey, note=body.note,
                      **_idkey_fields(body.idkey))
    return {"ok": True, "idkey": body.idkey, "note": body.note}
