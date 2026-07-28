"""Where a game lives on disk, and how it reaches the browser.

A game is a folder `<run_dir>/game/` of plain browser files — `index.html` plus whatever scripts,
styles and data the model wrote beside it. There is no build step and no transform: staging copies
the folder to `runtime/games/<slug>/`, which is what `/play` serves.
"""

import shutil
from pathlib import Path

RUNTIME_DIR = Path(__file__).resolve().parents[3] / "runtime"
GAME_DIR = "game"
ENTRY = "index.html"
_SOURCE_EXTS = (".html", ".js", ".mjs", ".css", ".json")


def game_dir(run_dir) -> Path:
    return Path(run_dir) / GAME_DIR


def entry_path(run_dir) -> Path:
    return game_dir(run_dir) / ENTRY


def game_files(run_dir) -> dict:
    """{relative path: source} for the files the model authored. Skips `_`-prefixed scratch."""
    d = game_dir(run_dir)
    if not d.exists():
        return {}
    out = {}
    for p in sorted(d.rglob("*")):
        if not p.is_file() or p.name.startswith("_") or p.suffix.lower() not in _SOURCE_EXTS:
            continue
        out[str(p.relative_to(d))] = p.read_text(encoding="utf-8", errors="replace")
    return out


def is_staged(slug: str) -> bool:
    return (RUNTIME_DIR / "games" / slug / ENTRY).exists()


def stage_for_play(run_dir, slug: str) -> str:
    """Publish the game folder, replacing any previous staging of the same run."""
    dst = RUNTIME_DIR / "games" / slug
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(game_dir(run_dir), dst, ignore=shutil.ignore_patterns("_*"))
    return f"games/{slug}/{ENTRY}"
