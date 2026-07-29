#!/usr/bin/env python3
"""Run a set of game requests through one harness on one model, and collect the artifacts.

The grid is (harness x model), each cell holding one folder per game. Nothing here grades
anything and nothing here edits a generated game: the output is an artifact to open in a
browser and judge by playing it.

  run_battery.py fetch unsloth/gemma-4-31B-it-GGUF gemma-4-31B-it-Q5_K_M
  run_battery.py run --harness iface --model gemma-4-31B-it-Q5_K_M
  run_battery.py index

Harnesses:
  naked    ~/Documents/naked/agent.py          - full transcript as memory, 5 tools, no contract
  compact  ~/Documents/naked/agent_compact.py  - naked + oldest-round compaction
  iface    ~/Documents/naked/build_iface.py    - interfaces -> review -> data -> per-function -> shell
  maestro  python -m maestro.codegen.run       - the real pipeline (kit, gates, audit, assets)
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
NAKED = Path.home() / "Documents" / "naked"
MODELS_DIR = Path.home() / "Documents" / "models" / "LLM"
GRID = Path.home() / "output" / "grid"
# Harnesses talk to the proxy, which applies the per-model request settings from
# model_settings.json over whatever the harness hardcoded, then forwards upstream.
ROUTER = os.environ.get("LLM_BASE", "http://localhost:8081")
UPSTREAM = os.environ.get("LLM_UPSTREAM", "http://localhost:8080")
LLAMA_SERVER = Path.home() / "Documents" / "llama.cpp-b10154" / "build" / "bin" / "llama-server"
# Maestro's deps (uvicorn, fastapi, ...) live in the repo venv, not the system python this
# script runs under; the naked harnesses are stdlib-only and don't care.
MAESTRO_PY = str(REPO / "venv" / "bin" / "python") if (REPO / "venv" / "bin" / "python").exists() \
    else sys.executable
SERVER_STATE = GRID / "_services" / "server_args.json"
# Where `python3 -m http.server` is serving runtime/ — maestro games only run through that harness.
RUNTIME_PORT = 8123

GAMES = [
    {"slug": "deckbuilder", "request":
     "A single-player deck-building roguelike like Slay the Spire: you fight a run of "
     "increasingly hard enemies one at a time, playing cards from a hand that costs energy, "
     "taking damage, and adding new cards to your deck between fights."},
    {"slug": "platformer", "request":
     "A side-scrolling platformer: run and jump across platforms, avoid or stomp enemies, "
     "collect pickups, and reach the goal at the end of the level. Keyboard controls."},
    {"slug": "arcade", "request":
     "A top-down arcade action game: you move around a single screen, enemies spawn and chase "
     "you, you shoot or dodge them, and you score points until you die."},
    {"slug": "gridturn", "request":
     "A turn-based grid roguelike: you move one tile per turn on a dungeon grid, monsters take "
     "their turn after you, you fight by moving into them, pick up items, and descend to "
     "deeper levels."},
]


def sh(cmd, **kw):
    return subprocess.run(cmd, shell=isinstance(cmd, str), **kw)


def router_models():
    try:
        with urllib.request.urlopen(f"{ROUTER}/v1/models", timeout=15) as r:
            return {m["id"] for m in json.load(r).get("data", [])}
    except Exception:
        return set()


def _alive(url, path="/v1/models", timeout=5):
    try:
        urllib.request.urlopen(url + path, timeout=timeout)
        return True
    except Exception:
        return False


def ensure_serving(model):
    """Bring up llama-server with THIS model's flags, and the proxy in front of it.

    Some settings are only honoured at server level - --reasoning-budget is ignored when sent
    per request - so a model whose flags differ from the running server needs a restart. The
    grid runs one model at a time, which is what makes that affordable."""
    sys.path.insert(0, str(REPO / "eval"))
    import sampler_proxy

    rules = sampler_proxy.load_rules()
    # A model served by another engine (ninfer) needs no llama-server at all, and starting one
    # would only contend for the VRAM that engine is already holding.
    external = sampler_proxy.settings_for(model, rules, "request").get("upstream")
    if external:
        if not _alive(external):
            raise SystemExit(f"{model} is served by {external}, which is not responding. "
                             f"Start that engine first.")
        print(f"  using external engine at {external} (no llama-server)")
    want = sampler_proxy.server_args_for(model, rules)
    SERVER_STATE.parent.mkdir(parents=True, exist_ok=True)
    have = json.loads(SERVER_STATE.read_text()) if SERVER_STATE.exists() else None
    running = bool(_procs(r"[l]lama-server"))
    if not external and (not running or have != want):
        for pid in _procs(r"[l]lama-server"):
            try:
                os.kill(pid, 15)
            except ProcessLookupError:
                pass
        time.sleep(4)
        log = open(GRID / "_services" / "llama-server.log", "a")
        # KV size is per-architecture, not per-file-size: granite-4.1-30b wanted 16 GiB of KV at
        # 65536 and OOMed on a 32 GB card that holds Qwen3.6-27B at the same context, because
        # Qwen's GQA makes its cache 4x smaller. A q8_0 cache at 32768 fits every candidate, and
        # no harness prompt here has come close to 32K. --models-max 1 stops the router holding
        # the previous model's VRAM while it loads the next one.
        subprocess.Popen(
            [str(LLAMA_SERVER), "--models-dir", str(MODELS_DIR), "--host", "127.0.0.1",
             "--port", "8080", "-ngl", "99", "-c", "32768", "--jinja",
             "--models-max", "1", "-ctk", "q8_0", "-ctv", "q8_0"] + want,
            stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        for _ in range(90):
            time.sleep(2)
            if _alive(UPSTREAM):
                break
        SERVER_STATE.write_text(json.dumps(want))
        print(f"  llama-server restarted with: {' '.join(want)}")

    if not _alive(ROUTER):
        log = open(GRID / "_services" / "proxy.log", "a")
        subprocess.Popen([sys.executable, str(REPO / "eval" / "sampler_proxy.py"),
                          "--port", ROUTER.rsplit(":", 1)[-1], "--upstream", UPSTREAM],
                         stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        for _ in range(30):
            time.sleep(1)
            if _alive(ROUTER):
                break


def wait_model(model, timeout=900):
    """Force the router to load the model, and prove it answers before anything is measured."""
    body = json.dumps({"model": model, "messages": [{"role": "user", "content": "say ready"}],
                       "max_tokens": 8}).encode()
    req = urllib.request.Request(f"{ROUTER}/v1/chat/completions", data=body,
                                 headers={"Content-Type": "application/json"})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=timeout) as r:
        json.load(r)
    return time.time() - t0


def _naked_like(script, model, request, out, timeout):
    env = dict(os.environ, LLM_BASE=ROUTER, LLM_MODEL=model)
    return subprocess.run(
        [sys.executable, str(NAKED / script), request, "--out", str(out), "--model", model],
        env=env, capture_output=True, text=True, timeout=timeout)


def run_naked(model, request, out, timeout):
    return _naked_like("agent.py", model, request, out, timeout)


def run_compact(model, request, out, timeout):
    return _naked_like("agent_compact.py", model, request, out, timeout)


def run_iface(model, request, out, timeout):
    # build_iface takes the model from the environment only; it has no --model flag.
    env = dict(os.environ, LLM_BASE=ROUTER, LLM_MODEL=model)
    return subprocess.run(
        [sys.executable, str(NAKED / "build_iface.py"), request, "--out", str(out)],
        env=env, capture_output=True, text=True, timeout=timeout)


def _settings_model(model):
    """Maestro reads the model from settings.json and caches the connector in-process, so the
    backend and worker must be restarted for a model swap to take effect."""
    path = REPO / "src" / "config" / "settings.json"
    cfg = json.loads(path.read_text())
    if cfg.get("llm", {}).get("model") == model:
        return False
    cfg.setdefault("llm", {})["model"] = model
    path.write_text(json.dumps(cfg, indent=1))
    return True


def _procs(pattern):
    """PIDs whose cmdline matches, never our own process tree. A bare `pgrep -f run.py` also
    matches the shell that is running the pgrep, so pkill on it kills the caller."""
    out = subprocess.run(["pgrep", "-f", pattern], capture_output=True, text=True).stdout
    mine = {os.getpid(), os.getppid()}
    return [int(p) for p in out.split() if p.isdigit() and int(p) not in mine]


def _services_up(model):
    changed = _settings_model(model)
    running = bool(_procs(r"[r]un\.py"))
    worker = bool(_procs(r"[w]orker\.agent"))
    if running and worker and not changed:
        return
    for pat in (r"[r]un\.py", r"[w]orker\.agent"):
        for pid in _procs(pat):
            try:
                os.kill(pid, 15)
            except ProcessLookupError:
                pass
    time.sleep(3)
    logs = GRID / "_services"
    logs.mkdir(parents=True, exist_ok=True)
    subprocess.Popen([MAESTRO_PY, "run.py"], cwd=REPO,
                     stdout=open(logs / "backend.log", "a"), stderr=subprocess.STDOUT)
    token = json.loads((REPO / "src" / "config" / "settings.json").read_text())["workqueue"]["token"]
    subprocess.Popen([MAESTRO_PY, "-m", "worker.agent", "--server", "http://localhost:8000",
                      "--token", token, "--queue", "llm", "--target", ROUTER],
                     cwd=REPO / "src",
                     stdout=open(logs / "worker.log", "a"), stderr=subprocess.STDOUT)
    for _ in range(60):
        time.sleep(2)
        try:
            urllib.request.urlopen("http://localhost:8000/api/system/health", timeout=5)
            break
        except urllib.error.HTTPError:
            break      # 401 from the auth gate still proves the server is answering
        except Exception:
            continue
    time.sleep(5)


def run_maestro(model, request, out, timeout):
    _services_up(model)
    res = subprocess.run([MAESTRO_PY, "-m", "maestro.codegen.run", request],
                         cwd=REPO / "src", capture_output=True, text=True, timeout=timeout)
    # The run dir is Maestro's, not ours; copy the artifact into the grid cell so every
    # harness's output sits in the same shape.
    m = re.search(r"runtime/index\.html\?game=(\S+)", res.stdout) or \
        re.search(r"runs/([0-9a-f]{8,})/", res.stdout)
    if m:
        run_id = m.group(1)
        (out / "_run_id").write_text(run_id)
        for src in (Path.home() / "output" / "runs" / run_id,
                    REPO / "runtime" / "games" / run_id):
            if src.exists():
                shutil.copytree(src, out / src.parent.name, dirs_exist_ok=True)
    return res


HARNESSES = {"naked": run_naked, "compact": run_compact,
             "iface": run_iface, "maestro": run_maestro}
TIMEOUTS = {"naked": 3600, "compact": 3600, "iface": 5400, "maestro": 10800}


def cmd_fetch(a):
    """Download one quant into the router's models dir, flattening shards into it."""
    dest = MODELS_DIR / a.repo.split("/")[-1]
    cmd = [str(Path.home() / ".local" / "bin" / "hf"), "download", a.repo,
           "--include", f"*{a.quant}*", "--local-dir", str(dest)]
    print(" ".join(cmd))
    if sh(cmd).returncode != 0:
        return 1
    for g in dest.rglob("*.gguf"):
        link = MODELS_DIR / g.name
        if not link.exists():
            link.symlink_to(g)
            print(f"  linked {link.name}")
    return 0


def cmd_run(a):
    games = json.loads(Path(a.games).read_text()) if a.games else GAMES
    if a.only:
        games = [g for g in games if g["slug"] in a.only.split(",")]
    root = Path(a.out) / a.model / a.harness
    root.mkdir(parents=True, exist_ok=True)
    ensure_serving(a.model)
    load = wait_model(a.model)
    print(f"model {a.model} ready in {load:.0f}s")
    manifest = root / "_cell.jsonl"
    for g in games:
        out = root / g["slug"]
        if (out / "_done.json").exists() and not a.force:
            print(f"  skip {g['slug']} (done)")
            continue
        out.mkdir(parents=True, exist_ok=True)
        print(f"  {a.harness}/{a.model}/{g['slug']} ...", flush=True)
        t0 = time.time()
        try:
            res = HARNESSES[a.harness](a.model, g["request"], out, TIMEOUTS[a.harness])
            code, err = res.returncode, res.stderr[-4000:]
            (out / "_stdout.log").write_text(res.stdout or "")
        except subprocess.TimeoutExpired:
            code, err = -9, "TIMEOUT"
        rec = {"harness": a.harness, "model": a.model, "slug": g["slug"],
               "exit": code, "seconds": round(time.time() - t0)}
        (out / "_done.json").write_text(json.dumps(rec, indent=1))
        if err:
            (out / "_stderr.log").write_text(err)
        with open(manifest, "a") as fh:
            fh.write(json.dumps(rec) + "\n")
        print(f"    exit={code} {rec['seconds']}s")
        # Rebuild after EVERY cell, not every row: a grid that only appears once a 3-harness row
        # finishes is invisible for hours, and the point of it is playing games as they land.
        cmd_index(argparse.Namespace(out=a.out))
    return 0


def cmd_screen(a):
    """The VIABILITY question, answered by one cell instead of a grid.

    A model earns its 12-cell grid only after showing it can produce an artifact in a wall time
    worth the quality. Measured: Ornith-1.0-9B took 23 min/game to make broken games, and the
    setting that made it 2.4x faster made its JSON malformed - no setting was both. Finding that
    out cost 80 minutes of grid instead of one 10-minute cell."""
    a.harness = a.harness or "iface"
    a.only = a.only or GAMES[0]["slug"]
    a.games = None
    a.force = True
    rc = cmd_run(a)
    root = Path(a.out) / a.model / a.harness / a.only
    rec = json.loads((root / "_done.json").read_text()) if (root / "_done.json").exists() else {}
    entry = next((p for p in (root / "index.html", root / "game" / "index.html")
                  if p.exists()), None)
    line = {"model": a.model, "harness": a.harness, "game": a.only,
            "seconds": rec.get("seconds"), "exit": rec.get("exit"),
            "artifact": bool(entry)}
    with open(Path(a.out) / "_screen.jsonl", "a") as fh:
        fh.write(json.dumps(line) + "\n")
    mins = (line["seconds"] or 0) / 60
    print(f"\nSCREEN {a.model}: {mins:.1f} min  exit={line['exit']}  "
          f"artifact={'yes' if line['artifact'] else 'NONE'}")
    print("  -> judge it by playing it; a cheap artifact still has to be worth playing.")
    return rc


PAGE = """<!doctype html><meta charset=utf-8><title>harness x model grid</title>
<style>
body{background:#14161a;color:#dfe3ea;font:14px/1.5 system-ui,sans-serif;margin:24px}
h1{font-size:18px;font-weight:600}
table{border-collapse:collapse;margin-top:16px}
th,td{border:1px solid #2b303a;padding:8px 10px;vertical-align:top}
th{background:#1c2028;font-weight:600;text-align:left}
a{color:#7db2ff;text-decoration:none}a:hover{text-decoration:underline}
.g{display:block;margin:2px 0}
.t{color:#7f8895;font-size:12px}
.bad{color:#e0645a}
img{display:block;width:200px;border:1px solid #2b303a;margin-top:4px;border-radius:3px}
</style>
<h1>harness &times; model &mdash; %(n)d games each</h1>
<p class=t>Open a game to play it. Nothing here has been graded or repaired.</p>
%(table)s
"""


def cmd_index(a):
    root = Path(a.out)
    root.mkdir(parents=True, exist_ok=True)
    cells = {}
    models, harnesses = [], []
    for done in sorted(root.glob("*/*/*/_done.json")):
        rec = json.loads(done.read_text())
        m, h, s = rec["model"], rec["harness"], rec["slug"]
        if m not in models:
            models.append(m)
        if h not in harnesses:
            harnesses.append(h)
        cells.setdefault((m, h), []).append((s, rec, done.parent))
    rows = ["<tr><th>model</th>" + "".join(f"<th>{h}</th>" for h in harnesses) + "</tr>"]
    for m in models:
        tds = []
        for h in harnesses:
            out = []
            for s, rec, d in sorted(cells.get((m, h), [])):
                # A maestro game is not self-contained: it is played through the runtime harness,
                # which loads runtime/games/<run_id>/main.js. Link that instead of a local file.
                run_id = (d / "_run_id").read_text().strip() if (d / "_run_id").exists() else None
                if run_id:
                    href = f"http://localhost:{RUNTIME_PORT}/index.html?game={run_id}"
                else:
                    entry = next((p for p in (d / "index.html", d / "game" / "index.html")
                                  if p.exists()), None)
                    href = os.path.relpath(entry, root) if entry else None
                bad = "" if rec["exit"] == 0 else " class=bad"
                link = f'<a href="{href}">{s}</a>' if href else f'<span{bad}>{s}</span>'
                shot = next((p for p in d.rglob("02_played.png")), None)
                img = f'<img src="{os.path.relpath(shot, root)}">' if shot else ""
                out.append(f'<span class=g>{link} <span class=t>'
                           f'{rec["seconds"]}s exit={rec["exit"]}</span>{img}</span>')
            tds.append("<td>" + ("".join(out) or "<span class=t>-</span>") + "</td>")
        rows.append(f"<tr><th>{m}</th>" + "".join(tds) + "</tr>")
    table = "<table>" + "".join(rows) + "</table>"
    (root / "index.html").write_text(PAGE % {"n": len(GAMES), "table": table})
    print(f"wrote {root / 'index.html'}  ({len(models)} models x {len(harnesses)} harnesses)")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    f = sub.add_parser("fetch", help="download a quant into the router's models dir")
    f.add_argument("repo")
    f.add_argument("quant")
    f.set_defaults(fn=cmd_fetch)

    r = sub.add_parser("run", help="run the games through one harness on one model")
    r.add_argument("--harness", required=True, choices=sorted(HARNESSES))
    r.add_argument("--model", required=True)
    r.add_argument("--games", help="JSON list of {slug,request}; defaults to the built-in four")
    r.add_argument("--only", help="comma-separated slugs to run")
    r.add_argument("--out", default=str(GRID))
    r.add_argument("--force", action="store_true", help="re-run cells already marked done")
    r.set_defaults(fn=cmd_run)

    s = sub.add_parser("screen", help="viability: ONE game on one harness, timed")
    s.add_argument("--model", required=True)
    s.add_argument("--harness", default="iface", choices=sorted(HARNESSES))
    s.add_argument("--only", default=None, help="game slug; defaults to the first")
    s.add_argument("--out", default=str(GRID))
    s.set_defaults(fn=cmd_screen)

    i = sub.add_parser("index", help="write the browsable grid page")
    i.add_argument("--out", default=str(GRID))
    i.set_defaults(fn=cmd_index)

    a = ap.parse_args()
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
