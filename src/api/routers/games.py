"""Games router — read-only browse of build runs.

A "game" is one run dir under <working_directory>/runs/<run_id>/. The list is
intentionally cheap (spec read only — no validate, which would trigger a Ren'Py
compile per row). The detail view runs validate once on demand to surface the to-do.
Build orchestration (freeze/build/streaming) lives in the next phase.
"""

import logging
import threading
from typing import Dict, List

from fastapi import APIRouter, HTTPException

logger = logging.getLogger(__name__)
router = APIRouter()

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
    from maestro.validate import validate

    state = RunState.for_run(run_id)
    spec_data = state.read_spec()
    if spec_data is None:
        raise HTTPException(status_code=404, detail=f"no game {run_id!r}")

    spec = Spec(spec_data)
    return {
        "run_id": run_id,
        "spec": spec_data,
        "artifact": state.load_artifact(),
        "todo": validate(spec, state),
        "frozen": spec.frozen,
        "built": _is_built(state.run_dir),
        "building": run_id in _active_builds,
    }


@router.post("/{run_id}/freeze", response_model=Dict)
async def freeze_game(run_id: str):
    """Human approval action — freeze the spec so the build can run."""
    from maestro.spec_tools import freeze_spec
    from maestro.state import RunState

    if RunState.for_run(run_id).read_spec() is None:
        raise HTTPException(status_code=404, detail=f"no game {run_id!r}")
    return freeze_spec(run_id)


@router.post("/{run_id}/build", response_model=Dict)
async def build_game(run_id: str):
    """Kick a build on a background thread. Progress streams over the websocket."""
    from maestro.run import run_build
    from maestro.state import RunState

    spec_data = RunState.for_run(run_id).read_spec()
    if spec_data is None:
        raise HTTPException(status_code=404, detail=f"no game {run_id!r}")
    if not spec_data.get("frozen"):
        raise HTTPException(status_code=400, detail="freeze the spec before building")

    with _active_lock:
        if run_id in _active_builds:
            raise HTTPException(status_code=409, detail="build already in progress")
        _active_builds.add(run_id)

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
