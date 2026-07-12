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


def run_probe(run_dir) -> dict:
    """Run the generic correctness invariants. `{"ok": True}` = clean; else `violations` each carry
    a `kind` + an actionable `detail` the fix feeds back."""
    game = game_path(run_dir)
    if not game.exists():
        return {"ok": False, "violations": [{"kind": "missing", "detail": f"{GAME_FILE} not written yet"}]}
    try:
        p = _run("probe.mjs", [str(game)])
    except subprocess.TimeoutExpired:
        return {"ok": False, "violations": [{"kind": "timeout",
                "detail": "probe did not finish (likely an infinite loop in update)"}]}
    try:
        return json.loads(p.stdout)
    except Exception:
        return {"ok": False, "violations": [{"kind": "runner", "detail": (p.stdout + p.stderr)[-400:]}]}
