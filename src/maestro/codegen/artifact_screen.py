"""The artifact text gate: does the finished game's own text carry hard-illegal content?

Runs at finalize, before staging — the backstop behind the input seams, since the model authors
text no prompt screen ever saw. Same narrow screen as every other seam (`tools/safety.py`): the
CSAM combination only, never taste. A hit HOLDS the build — no staging, no archive — and the
owner sees a neutral message rather than the filter's reasoning.

Only the model's own files are read: the vendored renderer is not the game's text, and binary
assets have no text to screen.
"""

from typing import Optional, Tuple

from maestro.codegen.staging import game_dir, is_vendored
from tools.safety import SafetyViolation, screen_text

TEXT_SUFFIXES = {".html", ".js", ".css", ".json", ".txt", ".md", ".xml", ".svg"}


def screen_artifact(run_dir) -> Optional[Tuple[str, SafetyViolation]]:
    """The first (relative path, violation) in the game folder's authored text, or None."""
    d = game_dir(run_dir)
    if not d.is_dir():
        return None
    for p in sorted(d.rglob("*")):
        if not p.is_file() or p.suffix.lower() not in TEXT_SUFFIXES or is_vendored(p, d):
            continue
        violation = screen_text(p.read_text(encoding="utf-8", errors="replace"))
        if violation is not None:
            return str(p.relative_to(d)), violation
    return None
