"""Where a game lives on disk, and how it reaches the browser.

A game is a folder `<run_dir>/game/` of plain browser files — `index.html` plus whatever scripts,
styles and data the model wrote beside it, and the vendored renderer `seed_vendor` put there before
it started. There is no build step and no transform: staging copies the folder to
`runtime/games/<slug>/`, which is what `/play` serves.
"""

import shutil
from pathlib import Path

RUNTIME_DIR = Path(__file__).resolve().parents[3] / "runtime"
GAME_DIR = "game"
ENTRY = "index.html"
_SOURCE_EXTS = (".html", ".js", ".mjs", ".css", ".json")


def game_dir(run_dir) -> Path:
    return Path(run_dir) / GAME_DIR


def seed_vendor(run_dir) -> None:
    """Copy the vendored renderer into the game folder. A game may fetch nothing at runtime, so a
    3D one can only import three.js if it is already a file beside it. An existing copy is left
    alone: a re-seed (a fix, a resumed build) must not overwrite what the model edited."""
    d = game_dir(run_dir)
    d.mkdir(parents=True, exist_ok=True)
    for src in sorted((RUNTIME_DIR / "vendor").glob("*.js")):
        dst = d / src.name
        if not dst.exists():
            shutil.copy2(src, dst)


def entry_path(run_dir) -> Path:
    return game_dir(run_dir) / ENTRY


def game_files(run_dir) -> dict:
    """{relative path: source} for the files the model AUTHORED. Skips `_`-prefixed scratch and the
    vendored renderer: the audit judges this listing, and vendored source is not the game."""
    d = game_dir(run_dir)
    if not d.exists():
        return {}
    vendor = {p.name for p in (RUNTIME_DIR / "vendor").glob("*.js")}
    out = {}
    for p in sorted(d.rglob("*")):
        if not p.is_file() or p.name.startswith("_") or p.suffix.lower() not in _SOURCE_EXTS:
            continue
        if p.name in vendor:
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
