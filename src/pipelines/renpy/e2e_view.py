"""End-to-end grading view for the renpy pipeline.

E2e scoring grades the *artifact* — the game a player actually runs — not the
pipeline's intermediate JSON. For renpy the artifact is the built Ren'Py script,
which the build stage writes to game_output/game/script.rpy. The judge gets only
this file: dialogue, narration, menus, and scene/show staging, with no premise,
voice sheets, or story plan. It reads the game the way a player would.
"""

from pathlib import Path
from typing import Optional

# The script sits under game_output/game/ in a live pipeline working dir, and under
# game/ in a saved or exported Ren'Py project dir (e.g. eval/games/<name>). Accept both
# so the same reader serves e2e runs and `score game` on an already-built directory.
_SCRIPT_RELS = (
    Path("game_output") / "game" / "script.rpy",
    Path("game") / "script.rpy",
)


def e2e_artifact(working_dir) -> Optional[str]:
    """Return the built game script under working_dir, or None if absent."""
    base = Path(working_dir)
    for rel in _SCRIPT_RELS:
        path = base / rel
        if path.exists():
            return path.read_text(encoding="utf-8")
    return None
