"""The local gradient — pure-Node gates that grade a game's SIM (no browser, no critic).

`run_headless` steps the sim N frames catching crashes/divergence; `run_probe` runs the generic
invariant probe (controls-live, wall-clip, ...). Both shell out to the runtime's `.mjs` runners,
which import `engine.js` themselves and pass the kit in — a game file imports nothing, so it runs
from anywhere by absolute path. These are what the module's `runs`/`plays` checks call.
"""

import json
import re
import subprocess
from pathlib import Path

RUNTIME_DIR = Path(__file__).resolve().parents[3] / "runtime"
GAME_FILE = "game.js"


def game_path(run_dir) -> Path:
    return Path(run_dir) / GAME_FILE


def stage_for_play(run_dir, slug: str) -> str:
    """Copy a built game.js into the runtime's games/ dir so the browser harness can load it (a game
    file imports nothing, so a copy is self-contained). Returns the play URL query for index.html.
    A dev convenience until the frontend serves the run dir directly (Phase 5)."""
    src = game_path(run_dir)
    (RUNTIME_DIR / "games" / f"{slug}.js").write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
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
    game = game_path(run_dir)
    if not game.exists():
        return {"ok": False, "phase": "missing", "error": f"{GAME_FILE} not written yet"}
    try:
        p = _run("headless.mjs", [str(game), str(frames)])
    except subprocess.TimeoutExpired:
        return {"ok": False, "phase": "timeout",
                "error": f"sim did not finish {frames} frames in time (likely an infinite loop)"}
    try:
        return json.loads(p.stdout.strip().splitlines()[-1])
    except Exception:
        return {"ok": False, "phase": "runner", "error": (p.stdout + p.stderr)[-800:]}


def _run_violation_gate(run_dir, runner: str, hint: str) -> dict:
    """Shared body for the probe/render gates: run a `.mjs` that prints `{ok, violations}`."""
    game = game_path(run_dir)
    if not game.exists():
        return {"ok": False, "violations": [{"kind": "missing", "detail": f"{GAME_FILE} not written yet"}]}
    try:
        p = _run(runner, [str(game)])
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
