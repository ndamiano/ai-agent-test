"""Staged construction — the hardest system gets a whole build to itself, then the game grows.

Measured 2026-08-02/03 (docs/experiments.md pending; the artifact holds the record): given the
whole game at once, the model starves whichever system is hardest — a duel built alone earned a
dedicated 8-function AI module, the same duel inside the full request earned zero opponent code.
Seven staged chains across four genres produced the strongest games of the battery, every stage
loading clean, and the one comparison where the model planned its own stages produced one of the
strongest artifacts yet (the tower defense whose single-shot twin shipped with towers that never
fired).

The PLAN is one small llm call (prompts/stage_plan.txt): stage 1 is a complete playable game of
the core system, each later stage ADDS one system and names what must keep working. A plan that
cannot be made or parsed degrades to a single stage — the request as-is — because staging that
fails must never cost the user their build. The plan is stored beside the build
(runs/<id>/stages.json) with the ORIGINAL request, so what the person asked for is never lost to
what the planner made of it.

Stage advance rides the same post-finalize seam as the error gate, and runs strictly after it:
a stage is only added onto a game whose current stage loads clean.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

STATE_FILE = "stages.json"
STAGE_MAX_STEPS = 100   # the fix default of 40 nearly truncated a measured overworld stage

_PROMPT = Path(__file__).parent / "prompts" / "stage_plan.txt"
_STAGE = re.compile(r"STAGE (\d+):\s*(.*?)(?=STAGE \d+:|$)", re.S)


def plan(request: str, run_id: str) -> List[str]:
    """The stage list for a request — [request] itself when planning fails or answers one stage."""
    from llm_clients.connector import get_connector
    from llm_clients.message_builder import MessageBuilder
    from tools.execution_context import run_scope
    try:
        # The whole instruction rides as the USER message — a system-only message array is a 400
        # ("no user query found") on the local server, and the plan ask IS the user turn here.
        with run_scope(run_id):
            reply = get_connector().generate_with_tools(
                MessageBuilder("You plan browser-game builds.")
                .add_user(_PROMPT.read_text(encoding="utf-8").format(request=request))
                .build(), [], max_tokens=2000)
        text = ((reply.get("choices") or [{}])[0].get("message", {}) or {}).get("content") or ""
        stages = [m.group(2).strip() for m in _STAGE.finditer(text) if m.group(2).strip()]
        if len(stages) >= 2:
            return stages
        logger.warning("stage plan for %s came back with %d stage(s) — building unstaged",
                       run_id, len(stages))
    except Exception:
        logger.exception("stage plan failed for %s — building unstaged", run_id)
    return [request]


def save(run_dir, request: str, stages: List[str]) -> None:
    (Path(run_dir) / STATE_FILE).write_text(
        json.dumps({"request": request, "stages": stages, "next": 1}, indent=2),
        encoding="utf-8")


def saved(run_dir) -> Optional[Dict]:
    """The stored plan, or None — what the create page resumes an unbuilt enhancement from."""
    path = Path(run_dir) / STATE_FILE
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def next_note(run_dir) -> Optional[str]:
    """The next stage's note, advancing the cursor — None when there is no plan or it is spent."""
    path = Path(run_dir) / STATE_FILE
    if not path.exists():
        return None
    state = json.loads(path.read_text(encoding="utf-8"))
    if state["next"] >= len(state["stages"]):
        return None
    note = state["stages"][state["next"]]
    state["next"] += 1
    path.write_text(json.dumps(state, indent=2), encoding="utf-8")
    return note


def advance(run_id: str) -> bool:
    """Kick the next stage as a fix build. True if one was started."""
    from maestro.codegen import build_chain
    from maestro.state import RunState
    note = next_note(RunState(run_id).run_dir)
    if note is None:
        return False
    logger.info("stage advance: %s starting next stage", run_id)
    build_chain.kickoff(run_id, kind="fix", note=note, max_steps=STAGE_MAX_STEPS)
    return True
