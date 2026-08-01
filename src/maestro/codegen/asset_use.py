"""Does the game actually LOAD the art it asked for — read off the game's own source, statically.

Two facts, both mechanical, neither an opinion about the picture:

  UNREFERENCED  the build asked for an asset and nothing in the source names it. The render was
                paid for and no player will ever see it.
  MISSING       the source loads `assets/<something>` that was never asked for and is not on disk.
                That is a broken image in the shipped game, not a slow one — no top-up can fill it,
                because nothing ever requested it.

Measured over the 35 staged games (2026-08-01): 352 assets asked for, 128 rendered and never
referenced, 115 paths referenced that were never asked for. One card game rendered 67 assets, used
none of them, and shipped 114 paths under an `assets/cards/` folder that does not exist. Nothing in
the loop could see either half, because `generate_media` answers with a path and never learns
whether the path was used.

An asset still RENDERING is neither: it is in the manifest, so the source naming it is right and the
file arriving late is the asset stage working. Only a path in neither the manifest nor the folder is
missing.

Matching is deliberately generous in one direction and literal in the other, so the count
under-reports rather than invents work. An asset counts as referenced if the source contains its id
OR its path — a game that builds `"assets/" + id + ".png"` at runtime still names the id somewhere.
A path counts as missing only when it is written out literally, so a dynamically assembled one is
never guessed at.
"""

from __future__ import annotations

import logging
import re
from typing import Dict, List

from maestro.codegen.assets import read_manifest
from maestro.codegen.staging import game_dir

logger = logging.getLogger(__name__)

# The renderer we seed every game folder with. It is not the game's source and it talks about
# `assets/` paths in its own comments, which is exactly the false positive to avoid.
VENDORED = {"three.module.js", "GLTFLoader.js", "BufferGeometryUtils.js"}
SOURCE_SUFFIXES = {".html", ".js", ".css"}

_ASSET_REF = re.compile(r"assets/[A-Za-z0-9_./-]+\.(?:png|glb|jpg|jpeg|webp|gif|svg)")


def _source(run_dir) -> str:
    """Every file the MODEL wrote, concatenated. A file that will not read is skipped rather than
    raised: this runs inside the done-nudge and inside finalize, and losing the audit is not a
    reason to lose the turn or the build."""
    game = game_dir(run_dir)
    parts = []
    for p in sorted(game.rglob("*")):
        if p.suffix.lower() in SOURCE_SUFFIXES and p.name not in VENDORED and p.is_file():
            try:
                parts.append(p.read_text(encoding="utf-8", errors="ignore"))
            except OSError as e:
                logger.warning("asset_use: could not read %s: %s", p, e)
    return "\n".join(parts)


def audit(run_dir) -> Dict[str, List[str]]:
    """`{"unreferenced": [asset id, ...], "missing": ["assets/x.png", ...]}` — both sorted, and
    both empty for a game that wired up everything it asked for."""
    entries = read_manifest(run_dir)
    src = _source(run_dir)
    game = game_dir(run_dir)

    unreferenced = sorted(e["id"] for e in entries
                          if e["id"] not in src and e.get("file", "") not in src)

    asked = {e.get("file", "") for e in entries}
    missing = sorted({ref for ref in _ASSET_REF.findall(src)
                      if ref not in asked and not (game / ref).exists()})
    return {"unreferenced": unreferenced, "missing": missing}


def report(audit_result: Dict[str, List[str]], limit: int = 12) -> str:
    """The audit as something to SAY, or "" when there is nothing to say. Every line is a fact plus
    the two ways out, because a build told only that something is wrong contorts the game guessing
    which. Long lists are clipped and say so — a model handed sixty paths fixes the first few and
    loses the rest anyway."""
    out = []
    unref, missing = audit_result["unreferenced"], audit_result["missing"]
    if unref:
        out.append(f"You asked for art that nothing in the game loads: {_names(unref, limit)}. "
                   "Draw each one where it belongs at the path generate_media gave you, or if the "
                   "game no longer needs it, leave it.")
    if missing:
        out.append(f"The game loads art that was never asked for and is not there: "
                   f"{_names(missing, limit)}. Call generate_media for each one, or change the "
                   "code to stop loading it.")
    return " ".join(out)


def _names(items: List[str], limit: int) -> str:
    shown = ", ".join(items[:limit])
    return shown if len(items) <= limit else f"{shown} (and {len(items) - limit} more)"
