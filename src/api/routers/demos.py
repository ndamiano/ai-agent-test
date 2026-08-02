"""The public demo surface — the landing page's playable games.

Unauthenticated and read-only, limited to the run ids the OWNER listed in settings
(`demo_games`): curation is a deploy-time decision, never a flag a build can set. Playing costs
nothing meterable — the files are static and the grant is an in-memory entry — and the games run
on the play origin, which holds no API and no credential (auth/playgrants.py)."""

from typing import Dict, List

from fastapi import APIRouter, HTTPException

from auth import playgrants
from config.settings_manager import settings_manager
from db import store as db_store
from maestro.codegen.staging import is_staged
from maestro.state import RunState

router = APIRouter()


def _demo_ids() -> List[str]:
    return [str(x) for x in settings_manager.get_settings().get("demo_games") or []]


@router.get("", response_model=List[Dict])
async def list_demos():
    """Each demo with the prompt that made it — the product story is the pairing, not the game
    alone. A listed id that is not staged is skipped, not an error: the list lives in settings
    and the games live on disk, and a redeploy may see one before the other."""
    out = []
    for run_id in _demo_ids():
        if not is_staged(run_id):
            continue
        row = db_store.game(run_id) or {}
        spec = RunState(run_id).read_spec() or {}
        out.append({"run_id": run_id,
                    "title": row.get("title") or run_id,
                    "prompt": spec.get("request", "")})
    return out


@router.post("/{run_id}/play-session", response_model=Dict)
async def demo_play_session(run_id: str):
    """A play session anyone may mint, for listed games only. The grant is issued under the
    game's owner, which grants nothing beyond this one game's static files."""
    if run_id not in _demo_ids():
        raise HTTPException(status_code=404, detail="not a demo")
    if not is_staged(run_id):
        raise HTTPException(status_code=409, detail="not built")
    owner = db_store.owner_of(run_id)
    if owner is None:
        raise HTTPException(status_code=404, detail="not a demo")
    token = playgrants.issue_handoff(owner, run_id)
    if token is None:
        raise HTTPException(status_code=429, detail="too many open sessions — try again shortly")
    origin = (settings_manager.get_settings().get("play") or {}).get("origin", "").rstrip("/")
    return {"url": f"{origin}/handoff?t={token}", "origin": origin}
