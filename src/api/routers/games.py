"""Games router — read-only browse of build runs.

A "game" is one run dir under <working_directory>/runs/<run_id>/. The list is
intentionally cheap (spec read only — no validate, which would trigger a Ren'Py
compile per row). The detail view runs validate once on demand to surface the to-do.
Build orchestration (freeze/build/streaming) lives in the next phase.
"""

import logging
from typing import Dict, List

from fastapi import APIRouter, HTTPException

logger = logging.getLogger(__name__)
router = APIRouter()


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
    }
