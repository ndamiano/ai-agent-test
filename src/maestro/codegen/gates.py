"""The local gradient — pure-Node gates that grade a game's SIM (no browser, no critic).

A game is a FOLDER `<run_dir>/game/`: an entry `main.js` (exports createGame) plus any system files
it imports, plus `manifest.json` (the code contract). The gates load `main.js`; Node resolves its
`./*.js` imports from the folder, so a multi-file game runs headless exactly like a one-file one.
The runners import `engine.js` themselves and pass the kit in.
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

RUNTIME_DIR = Path(__file__).resolve().parents[3] / "runtime"
GAME_DIR = "game"
ENTRY = "main.js"
MANIFEST = "manifest.json"


def game_dir(run_dir) -> Path:
    return Path(run_dir) / GAME_DIR


def entry_path(run_dir) -> Path:
    return game_dir(run_dir) / ENTRY


def manifest_path(run_dir) -> Path:
    return game_dir(run_dir) / MANIFEST


def read_manifest(run_dir) -> dict:
    p = manifest_path(run_dir)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def game_files(run_dir) -> dict:
    """{name: source} for every .js file in the game folder (entry + systems)."""
    d = game_dir(run_dir)
    return {p.name: p.read_text(encoding="utf-8") for p in sorted(d.glob("*.js"))} if d.exists() else {}


def stage_for_play(run_dir, slug: str) -> str:
    """Copy the whole game/ folder into runtime/games/<slug>/ so the browser harness can load its
    module graph. Returns the play URL query for index.html."""
    dst = RUNTIME_DIR / "games" / slug
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(game_dir(run_dir), dst, ignore=shutil.ignore_patterns(MANIFEST))
    return f"index.html?game={slug}"


def extract_code(text: str) -> str:
    """Pull the ```js block out of a model reply (the proven authoring shape — a fenced block, not a
    tool-call argument). Falls back to the whole reply when unfenced."""
    m = re.search(r"```(?:js|javascript)?\s*\n(.*?)```", text, re.S)
    return (m.group(1) if m else text).strip()


def _run(runner: str, args, timeout: int = 90) -> subprocess.CompletedProcess:
    return subprocess.run(["node", runner, *args], cwd=RUNTIME_DIR,
                          capture_output=True, text=True, timeout=timeout)


def run_headless(run_dir, frames: int = 900) -> dict:
    """Step the sim `frames` frames in pure Node. `{"ok": True}` = ran/resolved clean;
    `{"ok": False, ...}` carries the crash/divergence the fix feeds back."""
    entry = entry_path(run_dir)
    if not entry.exists():
        return {"ok": False, "phase": "missing", "error": f"{ENTRY} not written yet"}
    try:
        p = _run("headless.mjs", [str(entry), str(frames)])
    except subprocess.TimeoutExpired:
        return {"ok": False, "phase": "timeout",
                "error": f"sim did not finish {frames} frames in time (likely an infinite loop)"}
    try:
        return json.loads(p.stdout.strip().splitlines()[-1])
    except Exception:
        return {"ok": False, "phase": "runner", "error": (p.stdout + p.stderr)[-800:]}


def _run_violation_gate(run_dir, runner: str, hint: str) -> dict:
    """Shared body for the probe/render/scroll gates: run a `.mjs` that prints `{ok, violations}`."""
    entry = entry_path(run_dir)
    if not entry.exists():
        return {"ok": False, "violations": [{"kind": "missing", "detail": f"{ENTRY} not written yet"}]}
    try:
        p = _run(runner, [str(entry)])
    except subprocess.TimeoutExpired:
        return {"ok": False, "violations": [{"kind": "timeout", "detail": hint}]}
    try:
        return json.loads(p.stdout)
    except Exception:
        return {"ok": False, "violations": [{"kind": "runner", "detail": (p.stdout + p.stderr)[-400:]}]}


def run_probe(run_dir) -> dict:
    """Run the generic correctness invariants (controls live, no wall-clip). `{"ok": True}` = clean;
    else `violations` each carry a `kind` + an actionable `detail` the fix feeds back."""
    return _run_violation_gate(run_dir, "probe.mjs",
                               "probe did not finish (likely an infinite loop in update)")


def run_render(run_dir) -> dict:
    """Render smoke — exercise the draw() path (2D) headless can't see: draw-time crashes + blank
    screens. 3D games pass through (their render is mesh-sync from shape tags, not draw())."""
    return _run_violation_gate(run_dir, "render.mjs",
                               "render did not finish (likely an infinite loop in draw)")


def run_scroll(run_dir) -> dict:
    """Camera/scroll smoke — a world bigger than the screen must be followed by a panning camera,
    else most of the level is off-screen (passes every sim gate yet is unplayable). Confined and
    wrap-around games never fire; 3D passes through."""
    return _run_violation_gate(run_dir, "scroll.mjs",
                               "scroll check did not finish (likely an infinite loop in update)")
