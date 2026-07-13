"""The local gradient — pure-Node gates that grade a game's SIM (no browser, no critic).

A game is a FOLDER `<run_dir>/game/` of TypeScript modules: an entry `main.ts` (exports createGame)
plus system files it imports, plus `manifest.json` (the code contract). The gates:
  1. TYPECHECK with `tsc --noEmit` against the ambient kit types (runtime/engine.d.ts) — this catches
     the whole class of cross-file/type bugs (missing exports, wrong data shapes, bad arg counts)
     BEFORE the game runs, with file:line attribution.
  2. BUNDLE `main.ts` → `main.js` with esbuild (sourcemap) — the runnable artifact.
  3. Run the bundle headless / probe / render / scroll (node --enable-source-maps, so a runtime
     crash stack names the .ts SOURCE file, not the bundle).
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

RUNTIME_DIR = Path(__file__).resolve().parents[3] / "runtime"
GAME_DIR = "game"
ENTRY_SRC = "main.ts"      # authored entry
ENTRY = "main.js"          # esbuild bundle (the runnable artifact)
MANIFEST = "manifest.json"
_TSC = RUNTIME_DIR / "node_modules" / ".bin" / "tsc"
_ESBUILD = RUNTIME_DIR / "node_modules" / ".bin" / "esbuild"
_ENGINE_DTS = RUNTIME_DIR / "engine.d.ts"
_TSCONFIG = {"compilerOptions": {"noEmit": True, "target": "ES2020", "module": "esnext",
                                 "moduleResolution": "bundler", "strict": False, "skipLibCheck": True,
                                 "allowImportingTsExtensions": True, "noImplicitAny": False},
             "include": ["*.ts"]}


def game_dir(run_dir) -> Path:
    return Path(run_dir) / GAME_DIR


def entry_src_path(run_dir) -> Path:
    return game_dir(run_dir) / ENTRY_SRC


def bundle_path(run_dir) -> Path:
    return game_dir(run_dir) / ENTRY


def manifest_path(run_dir) -> Path:
    return game_dir(run_dir) / MANIFEST


def read_manifest(run_dir) -> dict:
    p = manifest_path(run_dir)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def game_files(run_dir) -> dict:
    """{name: source} for every authored .ts file (entry + systems). Excludes .d.ts type stubs and
    the built .js bundle — those are gate artifacts the model neither writes nor reads."""
    d = game_dir(run_dir)
    if not d.exists():
        return {}
    return {p.name: p.read_text(encoding="utf-8")
            for p in sorted(d.glob("*.ts")) if not p.name.endswith(".d.ts")}


def _run(args, timeout: int = 90, source_maps: bool = False) -> subprocess.CompletedProcess:
    cmd = ["node", "--enable-source-maps", *args] if source_maps else ["node", *args]
    return subprocess.run(cmd, cwd=RUNTIME_DIR, capture_output=True, text=True, timeout=timeout)


# ── typecheck (the contract gate) ─────────────────────────────────────────────
_TSC_ERR = re.compile(r"^([A-Za-z0-9_.-]+\.ts)\((\d+),\d+\):\s*(error TS\d+: .*)$", re.M)


def typecheck(run_dir) -> list:
    """Run `tsc --noEmit` over the game's .ts files against the kit types. Returns [(file, message)]
    per error (deduped), empty when clean. Sets up a self-contained check dir: the ambient
    engine.d.ts + a tsconfig are dropped in the game folder (gate artifacts, git/stage-ignored)."""
    d = game_dir(run_dir)
    if not entry_src_path(run_dir).exists():
        return []
    shutil.copyfile(_ENGINE_DTS, d / "engine.d.ts")
    (d / "tsconfig.json").write_text(json.dumps(_TSCONFIG), encoding="utf-8")
    try:
        p = subprocess.run([str(_TSC), "--noEmit", "-p", str(d / "tsconfig.json")],
                           cwd=d, capture_output=True, text=True, timeout=120)
    except Exception as e:
        return [(ENTRY_SRC, f"typecheck runner failed: {e}")]
    out, seen = [], set()
    for m in _TSC_ERR.finditer(p.stdout + p.stderr):
        key = (m.group(1), m.group(3))
        if m.group(1) != "engine.d.ts" and key not in seen:
            seen.add(key)
            out.append((m.group(1), f"line {m.group(2)}: {m.group(3)}"))
    return out


def build_bundle(run_dir) -> dict:
    """esbuild main.ts (+ its imports) → main.js with a sourcemap. Returns {ok} or {ok:False,error}.
    Cheap (~1ms); the run gates call it so they always execute the current source."""
    entry, bundle = entry_src_path(run_dir), bundle_path(run_dir)
    if not entry.exists():
        return {"ok": False, "error": f"{ENTRY_SRC} not written yet"}
    try:
        p = subprocess.run([str(_ESBUILD), str(entry), "--bundle", "--format=esm",
                            "--sourcemap=inline", f"--outfile={bundle}"],
                           cwd=game_dir(run_dir), capture_output=True, text=True, timeout=60)
    except Exception as e:
        return {"ok": False, "error": f"bundle runner failed: {e}"}
    return {"ok": True} if p.returncode == 0 else {"ok": False, "error": (p.stderr or p.stdout)[-500:]}


def stage_for_play(run_dir, slug: str) -> str:
    """Build the bundle and copy it into runtime/games/<slug>/main.js for the browser harness (the
    bundle inlines the game's imports, so it's self-contained). Returns the play URL query."""
    build_bundle(run_dir)
    dst = RUNTIME_DIR / "games" / slug
    dst.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(bundle_path(run_dir), dst / ENTRY)
    return f"index.html?game={slug}"


def extract_code(text: str) -> str:
    """Pull the ```ts/js block out of a model reply (a fenced block, not a tool-call arg). Falls back
    to the whole reply when unfenced."""
    m = re.search(r"```(?:ts|typescript|js|javascript)?\s*\n(.*?)```", text, re.S)
    return (m.group(1) if m else text).strip()


# ── run gates (on the bundle) ─────────────────────────────────────────────────
def run_headless(run_dir, frames: int = 900) -> dict:
    """Build then step the sim `frames` frames in pure Node. `{"ok": True}` = ran/resolved clean;
    else the crash/divergence (stack names the .ts source via the sourcemap) the fix feeds back."""
    b = build_bundle(run_dir)
    if not b.get("ok"):
        return {"ok": False, "phase": "build", "error": b.get("error", "bundle failed")}
    try:
        p = _run(["headless.mjs", str(bundle_path(run_dir)), str(frames)], source_maps=True)
    except subprocess.TimeoutExpired:
        return {"ok": False, "phase": "timeout",
                "error": f"sim did not finish {frames} frames in time (likely an infinite loop)"}
    try:
        return json.loads(p.stdout.strip().splitlines()[-1])
    except Exception:
        return {"ok": False, "phase": "runner", "error": (p.stdout + p.stderr)[-800:]}


def _run_violation_gate(run_dir, runner: str, hint: str) -> dict:
    b = build_bundle(run_dir)
    if not b.get("ok"):
        return {"ok": False, "violations": [{"kind": "build", "detail": b.get("error", "bundle failed")}]}
    try:
        p = _run([runner, str(bundle_path(run_dir))], source_maps=True)
    except subprocess.TimeoutExpired:
        return {"ok": False, "violations": [{"kind": "timeout", "detail": hint}]}
    try:
        return json.loads(p.stdout)
    except Exception:
        return {"ok": False, "violations": [{"kind": "runner", "detail": (p.stdout + p.stderr)[-400:]}]}


def run_probe(run_dir) -> dict:
    return _run_violation_gate(run_dir, "probe.mjs",
                               "probe did not finish (likely an infinite loop in update)")


def run_render(run_dir) -> dict:
    return _run_violation_gate(run_dir, "render.mjs",
                               "render did not finish (likely an infinite loop in draw)")


def run_scroll(run_dir) -> dict:
    return _run_violation_gate(run_dir, "scroll.mjs",
                               "scroll check did not finish (likely an infinite loop in update)")
