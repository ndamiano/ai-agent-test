"""Games router — read-only browse of build runs.

A "game" is one run dir under <working_directory>/runs/<run_id>/. The list is
intentionally cheap (spec read only — no validate, which would trigger a Ren'Py
compile per row). The detail view runs validate once on demand to surface the to-do.
Build orchestration (freeze/build/streaming) lives in the next phase.
"""

import asyncio
import logging
import threading
from typing import Dict, List

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

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

# Run ids with a build thread in flight. Guards against double-builds and lets the
# UI show a "building" state on load (the live event stream covers the rest).
_active_builds: set = set()
_active_lock = threading.Lock()


def _runs_dir():
    from tools.execution_context import resolve_base_path
    return resolve_base_path() / "runs"


def _is_built(run_dir) -> bool:
    return (run_dir / "game_output").exists()


@router.get("", response_model=List[Dict])
async def list_games():
    """Lightweight summary of every run. No validate (avoids per-row compiles)."""
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
        games.append({
            "run_id": run_dir.name,
            "title": spec.get("title", ""),
            "frozen": bool(spec.get("frozen")),
            "built": _is_built(run_dir),
            "building": run_dir.name in _active_builds,
            "n_components": len(state.component_ids()),
            "mtime": run_dir.stat().st_mtime,
        })

    games.sort(key=lambda g: g["mtime"], reverse=True)
    return games


@router.get("/{run_id}", response_model=Dict)
async def get_game(run_id: str):
    """Full detail for one game: spec, built artifact, and the current to-do."""
    from maestro.spec import Spec
    from maestro.state import RunState
    from maestro.modules.human import effective_failures
    from maestro.run_control import get as get_control

    state = RunState.for_run(run_id)
    spec_data = state.read_spec()
    if spec_data is None:
        raise HTTPException(status_code=404, detail=f"no game {run_id!r}")

    spec = Spec(spec_data)
    ctrl = get_control(run_id)
    built = _is_built(state.run_dir)
    images = state.run_dir / "game_output" / "game" / "images"
    return {
        "run_id": run_id,
        "spec": spec_data,
        "artifact": state.load_artifact(),
        "todo": effective_failures(spec_data, state),
        "human_todos": state.read_human_todos(),
        "waivers": state.read_waivers(),
        "frozen": spec.frozen,
        "built": built,
        "building": run_id in _active_builds,
        "status": ctrl.status if ctrl else ("built" if built else "idle"),
        "auto_pause": ctrl.auto_pause if ctrl else False,
        "assets_exist": images.is_dir() and any(images.glob("*.png")),
    }


@router.patch("/{run_id}/spec", response_model=Dict)
async def amend_game_spec_route(run_id: str, body: AmendBody):
    """Edit a draft's plan before freeze — change modules / sizing / title. Un-freezes + re-resolves
    (foundation forced, deps expanded, engine re-derived). Returns the fresh detail."""
    from tools.spec_tools import amend_spec
    from maestro.state import RunState

    if RunState.for_run(run_id).read_spec() is None:
        raise HTTPException(status_code=404, detail=f"no game {run_id!r}")
    await asyncio.to_thread(amend_spec, run_id, body.changes, body.reason)
    return await get_game(run_id)


@router.post("/{run_id}/freeze", response_model=Dict)
async def freeze_game(run_id: str):
    """Human approval action — freeze the spec so the build can run."""
    from tools.spec_tools import freeze_spec
    from maestro.state import RunState

    if RunState.for_run(run_id).read_spec() is None:
        raise HTTPException(status_code=404, detail=f"no game {run_id!r}")
    return await asyncio.to_thread(freeze_spec, run_id)


@router.post("/{run_id}/build", response_model=Dict)
async def build_game(run_id: str, body: BuildBody = BuildBody()):
    """Kick a build on a background thread. Progress streams over the websocket."""
    from maestro.run import run_build
    from maestro.state import RunState

    spec_data = RunState.for_run(run_id).read_spec()
    if spec_data is None:
        raise HTTPException(status_code=404, detail=f"no game {run_id!r}")
    if not spec_data.get("frozen"):
        raise HTTPException(status_code=400, detail="freeze the spec before building")

    from maestro.run_control import get_or_create

    with _active_lock:
        if run_id in _active_builds:
            raise HTTPException(status_code=409, detail="build already in progress")
        _active_builds.add(run_id)
    # Register the control before the thread starts so an immediate pause/cancel finds it.
    get_or_create(run_id).set_auto_pause(body.auto_pause)

    def _run():
        try:
            run_build(run_id)
        except Exception:
            logger.exception("build failed for %s", run_id)
        finally:
            with _active_lock:
                _active_builds.discard(run_id)

    threading.Thread(target=_run, daemon=True, name=f"build-{run_id}").start()
    return {"status": "building", "run_id": run_id}


def _control(run_id: str):
    """The live control for an in-flight build, or 409 if nothing is building."""
    from maestro.run_control import get

    ctrl = get(run_id)
    if ctrl is None:
        raise HTTPException(status_code=409, detail="no build in progress for this run")
    return ctrl


@router.post("/{run_id}/pause", response_model=Dict)
async def pause_game(run_id: str):
    """Pause a running build — it halts at the next step boundary (state stays consistent)."""
    _control(run_id).request_pause()
    return {"run_id": run_id, "status": "pausing"}


@router.post("/{run_id}/resume", response_model=Dict)
async def resume_game(run_id: str):
    """Resume a paused build."""
    _control(run_id).request_resume()
    return {"run_id": run_id, "status": "running"}


@router.post("/{run_id}/cancel", response_model=Dict)
async def cancel_game(run_id: str):
    """Cancel a running build — it unwinds at the next step boundary."""
    _control(run_id).request_cancel()
    return {"run_id": run_id, "status": "cancelling"}


@router.post("/{run_id}/auto-pause", response_model=Dict)
async def auto_pause_game(run_id: str, body: AutoPauseBody):
    """Arm/disarm auto-pause: when armed, the build parks itself each time a component finishes."""
    _control(run_id).set_auto_pause(body.enabled)
    return {"run_id": run_id, "auto_pause": body.enabled}


def _require_state(run_id: str):
    from maestro.state import RunState

    state = RunState.for_run(run_id)
    if state.read_spec() is None:
        raise HTTPException(status_code=404, detail=f"no game {run_id!r}")
    return state


@router.post("/{run_id}/todos", response_model=Dict)
async def add_todo_game(run_id: str, body: TodoBody):
    """Add a human todo against a component — the build won't complete while it's open.
    A live build parks in `awaiting_human` once its machine checks pass."""
    from maestro.modules.human import add_todo

    return add_todo(_require_state(run_id), body.component_id, body.text)


@router.patch("/{run_id}/todos/{todo_id}", response_model=Dict)
async def resolve_todo_game(run_id: str, todo_id: str, body: ResolveBody):
    """Mark a human todo done (or reopen it) — only the human arbitrates this."""
    from maestro.modules.human import resolve_todo

    if not resolve_todo(_require_state(run_id), todo_id, body.done):
        raise HTTPException(status_code=404, detail=f"no todo {todo_id!r}")
    return {"run_id": run_id, "todo_id": todo_id, "done": body.done}


@router.post("/{run_id}/waive", response_model=Dict)
async def waive_game(run_id: str, body: WaiveBody):
    """Accept a machine check still reported red — it leaves the to-do and no longer blocks
    completion. Keyed on the error's `idkey` (from the detail endpoint's todo list)."""
    from maestro.modules.human import waive

    return waive(_require_state(run_id), body.idkey, body.note)


@router.post("/{run_id}/unwaive", response_model=Dict)
async def unwaive_game(run_id: str, body: UnwaiveBody):
    """Reinstate a previously waived check."""
    from maestro.modules.human import unwaive

    if not unwaive(_require_state(run_id), body.idkey):
        raise HTTPException(status_code=404, detail=f"no waiver {body.idkey!r}")
    return {"run_id": run_id, "idkey": body.idkey}


def _require_editable(run_id: str):
    """Hand-edits race the executor thread on the same files, so only allow them when no
    build is running OR the build is parked (paused / awaiting_human)."""
    from maestro.run_control import get as get_control

    if run_id in _active_builds:
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


def _spec_state(run_id: str):
    from maestro.spec import Spec

    state = _require_state(run_id)
    return Spec(state.read_spec()), state


@router.put("/{run_id}/component/{component_id}", response_model=Dict)
async def edit_component_game(run_id: str, component_id: str, body: ComponentBody):
    """Human edit of a whole component (schema-validated; overrides the lock)."""
    _require_editable(run_id)
    spec, state = _spec_state(run_id)
    result = _human_tools(spec, state)["write_component"](component_id, body.content, force=True)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error", "invalid component"))
    return result


@router.put("/{run_id}/node/{node_id}", response_model=Dict)
async def edit_node_game(run_id: str, node_id: str, body: NodeEditBody):
    """Human patch of one node field (e.g. fix a character's line); overrides the lock."""
    _require_editable(run_id)
    spec, state = _spec_state(run_id)
    patch = body.model_dump(exclude_unset=True)
    result = _human_tools(spec, state)["edit_node"](node_id, force=True, **patch)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error", "invalid edit"))
    return result


@router.post("/{run_id}/regenerate-assets", response_model=Dict)
async def regenerate_assets_game(run_id: str):
    """Regenerate the art (ComfyUI when up, placeholder fallback) from the current manifest.
    Recompile afterward to repackage the project with the new images."""
    _require_editable(run_id)
    from renpy.fns import generate_images

    _, state = _spec_state(run_id)
    return generate_images(state.load_artifact(), state.run_dir) or {"ok": True}


@router.post("/{run_id}/compile", response_model=Dict)
async def compile_game(run_id: str, body: CompileBody = CompileBody()):
    """Compile/package the project on demand. Output served via /api/outputs/."""
    _require_editable(run_id)
    from maestro.engines import compile_for

    spec, state = _spec_state(run_id)
    return compile_for(spec.engine)(state.run_dir, distribute=body.distribute)


@router.post("/{run_id}/reveal", response_model=Dict)
async def reveal_game(run_id: str):
    """Open the run's folder in the host's file manager. Only works when the backend
    runs on the same machine as the user (it does — Maestro is a local app)."""
    import subprocess
    import sys

    state = _require_state(run_id)
    path = str(state.run_dir)
    opener = {"darwin": ["open"], "win32": ["explorer"]}.get(sys.platform, ["xdg-open"])
    try:
        subprocess.Popen([*opener, path])
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"could not open folder: {e}")
    return {"run_id": run_id, "path": path}


# Node rewrites in flight (one per node), so the UI can disable a node's button while it runs.
_rewriting: set = set()


@router.post("/{run_id}/node/{node_id}/rewrite", response_model=Dict)
async def rewrite_node_game(run_id: str, node_id: str, body: RewriteBody):
    """Regenerate one scene from a human note ('make it tenser'), on a background thread.
    Progress + completion stream over the websocket (node_rewrite_*)."""
    _require_state(run_id)
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
