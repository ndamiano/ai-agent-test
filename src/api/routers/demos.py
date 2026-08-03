"""The public demo surface — the landing page's playable games.

Unauthenticated and read-only, limited to the run ids the OWNER listed in settings
(`demo_games`): curation is a deploy-time decision, never a flag a build can set. The list is
TIERED — `showcase` is what the machine can achieve (staged builds, playtest notes), `oneshot`
is what a single sentence gets, kept as it first came out — and the tier is the owner's claim,
curated exactly like membership. Playing costs nothing meterable — the files are static and the
grant is an in-memory entry — and the games run on the play origin, which holds no API and no
credential (auth/playgrants.py)."""

from typing import Dict, List

from fastapi import APIRouter, HTTPException

from auth import playgrants
from config.settings_manager import settings_manager
from db import store as db_store
from maestro.codegen import stages
from maestro.codegen.staging import is_staged, staged_title
from maestro.state import RunState

router = APIRouter()


TIERS = ("showcase", "oneshot")


def _demo_tiers() -> Dict[str, List[str]]:
    cfg = settings_manager.get_settings().get("demo_games") or {}
    return {tier: [str(x) for x in cfg.get(tier) or []] for tier in TIERS}


def _demo_ids() -> List[str]:
    tiers = _demo_tiers()
    return [run_id for tier in TIERS for run_id in tiers[tier]]


@router.get("", response_model=List[Dict])
async def list_demos():
    """Each demo with the prompt that made it and the tier it was listed under — the product
    story is the pairing, not the game alone. A listed id that is not staged is skipped, not an
    error: the list lives in settings and the games live on disk, and a redeploy may see one
    before the other."""
    out = []
    tiers = _demo_tiers()
    for tier in TIERS:
        for run_id in tiers[tier]:
            if not is_staged(run_id):
                continue
            row = db_store.game(run_id) or {}
            state = RunState(run_id)
            spec = state.read_spec() or {}
            # A staged run's spec.request is stage-1 machine text; the person's own words live
            # in the stage plan, and the card's whole claim is "these words made this game".
            plan = stages.saved(state.run_dir) or {}
            out.append({"run_id": run_id,
                        "title": staged_title(run_id) or row.get("title") or run_id,
                        "prompt": plan.get("request") or spec.get("request", ""),
                        "tier": tier})
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
