"""Where a filled-in game grade is written, and how it comes back.

The form and its reasoning are `docs/game_rubric.md` — this module only stores what the grader
typed. Nothing here reads a grade back into the build path: a grade is a human judgement about
whether a game is GOOD, and the one rule the loop holds to is that nothing machine-side acts on
that. It is an instrument for the owner, not a signal for the model.

Grades live under `<data_dir>/grades/`, which is gitignored control-plane state rather than repo
content — one JSON file per grading, named for its run and the moment it was taken. A run graded
again after a fix build keeps BOTH files: re-grading is the point, and a rubric that overwrote its
own history could never show that a change moved a game.
"""

import json
import logging
import re
import time
from pathlib import Path
from typing import Any, Dict, List

from config.settings_manager import settings_manager

logger = logging.getLogger(__name__)

RUN_ID = re.compile(r"^[A-Za-z0-9_-]+$")


def _dir() -> Path:
    path = Path(settings_manager.get_settings()["data_dir"]).resolve() / "grades"
    path.mkdir(parents=True, exist_ok=True)
    return path


def save(run_id: str, grade: Dict[str, Any]) -> str:
    """Write one grading and answer with the name it was stored under."""
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    name = f"{run_id}__{stamp}.json"
    body = {**grade, "run_id": run_id, "graded_at": stamp}
    (_dir() / name).write_text(json.dumps(body, indent=2), encoding="utf-8")
    logger.info("grade saved run=%s file=%s", run_id, name)
    return name


def history(run_id: str) -> List[Dict[str, Any]]:
    """Every grading of one run, oldest first. A file that will not parse is skipped rather than
    raised: one corrupt grade must not hide the rest."""
    out = []
    for path in sorted(_dir().glob(f"{run_id}__*.json")):
        try:
            out.append(json.loads(path.read_text(encoding="utf-8")))
        except (ValueError, OSError):
            logger.warning("unreadable grade file: %s", path.name)
    return out
