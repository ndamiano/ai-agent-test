"""Where a game lives on disk, and how it reaches the browser.

A game is a folder `<run_dir>/game/` of plain browser files — `index.html` plus whatever scripts,
styles and data the model wrote beside it, and the vendored renderer `seed_vendor` put there before
it started. There is no build step and no transform: staging copies the folder to
`runtime/games/<slug>/`, which is what `/play` serves.
"""

import re
import shutil
from pathlib import Path
from typing import Optional

RUNTIME_DIR = Path(__file__).resolve().parents[3] / "runtime"
GAME_DIR = "game"
ENTRY = "index.html"


def game_dir(run_dir) -> Path:
    return Path(run_dir) / GAME_DIR


def seed_vendor(run_dir) -> None:
    """Copy the vendored renderer and the helper library into the game folder. A game may fetch
    nothing at runtime, so a 3D one can only import three.js if it is already a file beside it.
    An existing copy is left alone: a re-seed (a fix, a resumed build) must not overwrite what
    the model edited."""
    d = game_dir(run_dir)
    d.mkdir(parents=True, exist_ok=True)
    for src in sorted((RUNTIME_DIR / "vendor").glob("*.js")):
        dst = d / src.name
        if not dst.exists():
            shutil.copy2(src, dst)
    (d / "lib").mkdir(exist_ok=True)
    for src in sorted((RUNTIME_DIR / "vendor" / "lib").glob("*.js")):
        dst = d / "lib" / src.name
        if not dst.exists():
            shutil.copy2(src, dst)


def is_vendored(path: Path, game_dir: Path) -> bool:
    """Seeded by the platform, not written by the model: the renderer beside the entry and the
    helper library under lib/."""
    rel = path.relative_to(game_dir)
    return rel.suffix == ".js" and (RUNTIME_DIR / "vendor" / rel).is_file()


def has_authored_files(run_dir) -> bool:
    """Has a build written anything into the game folder? The vendored renderer is seeded there
    before the model runs, so its presence says nothing — this is what tells a run with a dead
    build's leftovers apart from one with an empty folder."""
    d = game_dir(run_dir)
    if not d.is_dir():
        return False
    return any(p.is_file() and not is_vendored(p, d) for p in d.rglob("*"))


def entry_path(run_dir) -> Path:
    return game_dir(run_dir) / ENTRY


def is_staged(slug: str) -> bool:
    return (RUNTIME_DIR / "games" / slug / ENTRY).exists()


_TITLE = re.compile(r"<title[^>]*>\s*(.*?)\s*</title>", re.IGNORECASE | re.DOTALL)


def staged_title(slug: str) -> Optional[str]:
    """The name the MODEL gave the game — its staged index.html's <title> — or None when there
    is no staging, no tag, or an empty one."""
    entry = RUNTIME_DIR / "games" / slug / ENTRY
    if not entry.is_file():
        return None
    match = _TITLE.search(entry.read_text(encoding="utf-8", errors="replace")[:4096])
    if not match:
        return None
    title = " ".join(match.group(1).split())
    return title[:80] or None


def stage_for_play(run_dir, slug: str) -> str:
    """Publish the game folder, replacing any previous staging of the same run."""
    dst = RUNTIME_DIR / "games" / slug
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(game_dir(run_dir), dst, ignore=shutil.ignore_patterns("_*"))
    return f"games/{slug}/{ENTRY}"
