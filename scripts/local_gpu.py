#!/usr/bin/env python3
"""Drain a queue on a one-card box: start the model, start the worker, wait, stop both.
`auto` holds whichever queue has work, swapping the card as the queues need it.
"""

import argparse
import datetime
import json
import os
import shlex
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

LOGS = Path(os.environ.get("MAESTRO_LOCAL_LOGS", "/tmp/maestro-local"))

NINFER_DEFAULT = Path("/home/nick/Documents/ninfer/build/apps/ninfer-serve")
NINFER_MODELS = Path(os.environ.get("NINFER_MODELS", "/var/lib/models/ninfer"))
COMFY_PYTHON = Path(os.environ.get("COMFY_PYTHON",
                                   "/home/nick/Documents/Comfy/comfy-env/bin/python"))
COMFY_DIR = Path(os.environ.get("COMFY_DIR", "/home/nick/comfy/mess-with-comfy"))
SAFETY_MODEL_DIR = os.environ.get("SAFETY_MODEL_DIR", "/home/nick/comfy-models/safety")


def _worker_python(queue: str) -> str:
    """Which interpreter runs a queue's worker — image and video need ComfyUI's environment."""
    if queue in ("image", "video") and COMFY_PYTHON.exists():
        return str(COMFY_PYTHON)
    return sys.executable
TRELLIS_PYTHON = Path(os.environ.get("TRELLIS_PYTHON",
                                     "/home/nick/cube3d-lab/trellis2-venv/bin/python"))
TRELLIS_REPO = Path(os.environ.get("TRELLIS_REPO", "/home/nick/cube3d-lab/trellis2"))
TRELLIS_WEIGHTS = Path(os.environ.get("TRELLIS_WEIGHTS", "/home/nick/cube3d-lab/trellis2-weights"))

IDLE_TICKS = 3          # empty polls before a queue counts as drained; a continuation
                        # is enqueued by the completion that just finished, so "empty
                        # once" is not empty
POLL_SECONDS = 2.0

QUEUES = ["llm", "image", "mesh", "video"]
PRIORITY = {"llm": 0, "image": 1, "mesh": 2, "video": 3}  # tie-break when nothing is currently held
# A pending job the control plane's reaper will fail before the held queue drains has to be served
# first: one card, a build whose turns re-fill the llm queue as fast as it empties, and five
# sheets waited 30 minutes behind it for a worker that never came (2026-09-04, two of five lost).
STARVE_SECONDS = 600
# How long a worker may keep the card to finish the job in flight when the card is handed over.
# Longer than a build turn: the alternative is throwing that turn away.
HANDOFF_SECONDS = 300


def _settings() -> dict:
    return json.loads((ROOT / "src" / "config" / "settings.json").read_text())


def _model_id() -> str:
    return (_settings().get("llm") or {}).get("model") or "qwen3.8_27b"


def _ninfer_bin() -> Path:
    """The engine built for the model variant the settings ask for; NINFER_BIN overrides."""
    if os.environ.get("NINFER_BIN"):
        return Path(os.environ["NINFER_BIN"])
    variant = _model_id().rsplit("_", 1)[-1]
    sibling = NINFER_DEFAULT.parents[2].with_name(f"ninfer-{variant}") / "build/apps/ninfer-serve"
    return sibling if sibling.exists() else NINFER_DEFAULT


def _ninfer_artifact() -> Path:
    path = NINFER_MODELS / f"{_model_id().replace('.', '_')}_nvfp4.ninfer"
    if path.exists():
        return path
    raise SystemExit(f"no ninfer artifact for model id {_model_id()!r} at {path}")


def _leg(queue: str) -> dict:
    if queue == "llm":
        return {
            "port": 8090,
            "ready": "http://127.0.0.1:8090/v1/models",
            "argv": [str(_ninfer_bin()), str(_ninfer_artifact()), "--model-id", _model_id(),
                     "--host", "127.0.0.1", "--port", "8090",
                     "--max-context", str((_settings().get("llm") or {}).get("n_ctx", 98304)),
                     "--spec", "mtp", "--draft-tokens", "3", "--lm-head-draft",
                     "--presence-penalty", "0", "--cors", "--vision",
                     # The same llm.ninfer_args a pod gets at create, so a flag that dies at
                     # launch dies here first.
                     *shlex.split((_settings().get("llm") or {}).get("ninfer_args") or "")],
            "cwd": None,
            "env": {},
        }
    if queue in ("image", "video"):
        # The same ComfyUI serves both: the image graphs and MiniMax's are nodes in one checkout.
        return {
            "port": 8188,
            "ready": "http://127.0.0.1:8188/system_stats",
            "argv": [str(COMFY_PYTHON), "main.py", "--port", "8188",
                     "--use-pytorch-cross-attention"],
            "cwd": str(COMFY_DIR),
            "env": {},
        }
    if queue == "mesh":
        return {
            "port": 8189,
            "ready": "http://127.0.0.1:8189/health",
            "argv": [str(TRELLIS_PYTHON), str(ROOT / "src" / "tools" / "trellis_server.py"),
                     "--repo", str(TRELLIS_REPO), "--weights", str(TRELLIS_WEIGHTS),
                     "--host", "127.0.0.1", "--port", "8189", "--stage-dir", ""],
            "cwd": None,
            "env": {},
        }
    raise SystemExit(f"unknown queue {queue!r}")


def _up(url: str, timeout: float = 2.0) -> bool:
    import urllib.error
    import urllib.request
    try:
        with urllib.request.urlopen(url, timeout=timeout):
            return True
    except (urllib.error.URLError, OSError):
        return False


def _ready(queue: str, url: str, timeout: float = 2.0) -> bool:
    """Readiness is server-specific: ninfer must list the configured model id, not just listen."""
    import urllib.error
    import urllib.request
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            body = resp.read()
    except (urllib.error.URLError, OSError):
        return False
    if queue == "mesh":
        # /health answers 200 the instant uvicorn is up, long before the background preload
        # thread finishes loading and warming the pipeline. A worker that starts on that alone
        # forwards a real job while the warmup's own generate() is still running — two
        # concurrent calls into TRELLIS's non-thread-safe CUDA extensions (CuMesh/o_voxel),
        # which killed the server process outright with no traceback. Wait for `warm`.
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            return False
        return bool(data.get("warm"))
    if queue != "llm":
        return True
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return False
    ids = {m.get("id") for m in data.get("data", [])}
    return _model_id() in ids


def _spawn(name: str, argv: list, cwd, env: dict) -> subprocess.Popen:
    LOGS.mkdir(parents=True, exist_ok=True)
    log = (LOGS / f"{name}.log").open("ab")
    print(f"[{name}] starting — log {LOGS / f'{name}.log'}", flush=True)
    return subprocess.Popen(argv, cwd=cwd, stdout=log, stderr=subprocess.STDOUT,
                            env={**os.environ, **env}, start_new_session=True)


def _stop(name: str, process: subprocess.Popen, grace: float = 25) -> None:
    if process.poll() is not None:
        return
    print(f"[{name}] stopping", flush=True)
    # the whole group: ComfyUI and the trellis server both fork children that keep
    # the card if only the parent is signalled
    try:
        os.killpg(os.getpgid(process.pid), signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=grace)
    except subprocess.TimeoutExpired:
        os.killpg(os.getpgid(process.pid), signal.SIGKILL)


def hand_over(held: Optional[str], server, worker) -> None:
    """Give the card up: the worker first, with time to finish its job, then the server."""
    if worker is not None:
        _stop(f"{held}-worker", worker, grace=HANDOFF_SECONDS)
    if server is not None:
        _stop(f"{held}-server", server)


def _await_ready(name: str, queue: str, url: str, process: subprocess.Popen, timeout: float) -> None:
    started = time.time()
    while time.time() - started < timeout:
        if process.poll() is not None:
            raise SystemExit(f"[{name}] died during startup — see {LOGS / f'{name}.log'}")
        if _ready(queue, url):
            print(f"[{name}] ready in {time.time() - started:.0f}s", flush=True)
            return
        time.sleep(1.0)
    raise SystemExit(f"[{name}] not ready after {timeout:.0f}s")


def _waiting(queue: str) -> int:
    from db import connection
    with connection.platform_db() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM jobs WHERE queue = ? AND status IN ('pending','claimed')",
            (queue,),
        ).fetchone()
    return int(row["n"])


def _oldest_wait(queue: str) -> float:
    """Seconds the queue's longest-unclaimed job has waited; 0 with nothing pending."""
    from db import connection
    with connection.platform_db() as conn:
        row = conn.execute(
            "SELECT MIN(created_at) AS t FROM jobs WHERE queue = ? AND status = 'pending'",
            (queue,),
        ).fetchone()
    return time.time() - row["t"] if row["t"] else 0.0


def drain(queue: str, *, keep: bool, ready_timeout: float) -> None:
    leg = _leg(queue)
    if _up(leg["ready"]):
        raise SystemExit(
            f"[{queue}] something is already serving port {leg['port']}. Stop it first — this "
            f"script only stops what it started."
        )

    waiting = _waiting(queue)
    print(f"[{queue}] {waiting} job(s) waiting", flush=True)

    server = _spawn(f"{queue}-server", leg["argv"], leg["cwd"], leg["env"])
    worker = None
    try:
        _await_ready(f"{queue}-server", queue, leg["ready"], server, ready_timeout)
        token = (_settings().get("workqueue") or {}).get("token") or ""
        worker = _spawn(
            f"{queue}-worker",
            [_worker_python(queue), "-m", "worker.agent", "--server", "http://localhost:8000",
             "--token", token, "--queue", queue,
             "--target", f"http://localhost:{leg['port']}"],
            str(ROOT / "src"),
            {"SAFETY_MODEL_DIR": SAFETY_MODEL_DIR} if queue in ("image", "video") else {},
        )

        idle = 0
        while idle < IDLE_TICKS:
            time.sleep(POLL_SECONDS)
            if worker.poll() is not None:
                raise SystemExit(f"[{queue}] worker exited — see {LOGS / f'{queue}-worker.log'}")
            left = _waiting(queue)
            if left:
                idle = 0
                if left != waiting:
                    print(f"[{queue}] {left} left", flush=True)
                    waiting = left
            else:
                idle += 1
        print(f"[{queue}] drained", flush=True)
    finally:
        if worker is not None:
            _stop(f"{queue}-worker", worker)
        if keep:
            print(f"[{queue}] leaving the model up (--keep)", flush=True)
        else:
            _stop(f"{queue}-server", server)


# ---------------------------------------------------------------------------
# auto mode
# ---------------------------------------------------------------------------

def choose_action(held: Optional[str], pending: dict, idle_ticks: dict,
                   idle_tick_limit: int, waits: Optional[dict] = None) -> Optional[str]:
    starving = {q: w for q, w in (waits or {}).items() if w >= STARVE_SECONDS and q != held}
    if starving:
        return max(starving, key=starving.get)
    if held is not None:
        if pending.get(held, 0) > 0 or idle_ticks.get(held, 0) < idle_tick_limit:
            return held
    candidates = [q for q in QUEUES if pending.get(q, 0) > 0]
    if not candidates:
        return None
    candidates.sort(key=lambda q: PRIORITY[q])
    return candidates[0]


def _log_auto(msg: str, logfile) -> None:
    stamp = datetime.datetime.now().isoformat(timespec="seconds")
    line = f"[{stamp}] {msg}"
    print(line, flush=True)
    logfile.write(line + "\n")
    logfile.flush()


def auto(idle_exit: Optional[float], ready_timeout: float) -> None:
    LOGS.mkdir(parents=True, exist_ok=True)
    auto_log = (LOGS / "auto.log").open("a")
    held: Optional[str] = None
    server: Optional[subprocess.Popen] = None
    worker: Optional[subprocess.Popen] = None
    idle_ticks = {q: 0 for q in QUEUES}
    all_idle_since: Optional[float] = None

    stopping = {"flag": False}

    def _signal(_signum, _frame):
        stopping["flag"] = True

    signal.signal(signal.SIGINT, _signal)
    signal.signal(signal.SIGTERM, _signal)

    def _stop_current():
        nonlocal server, worker, held
        hand_over(held, server, worker)
        worker = server = None
        held = None

    def _start(queue: str):
        nonlocal server, worker, held
        leg = _leg(queue)
        if _up(leg["ready"]):
            # Someone else's server on the port is the server to use, not a reason to stop: auto
            # would otherwise abandon a queue it cannot start and exit, and the card it was holding
            # goes with it. It stays THEIRS — nothing here stops what it did not start.
            print(f"[{queue}] using the server already on port {leg['port']}", flush=True)
            server = None
        else:
            server = _spawn(f"{queue}-server", leg["argv"], leg["cwd"], leg["env"])
            _await_ready(f"{queue}-server", queue, leg["ready"], server, ready_timeout)
        token = (_settings().get("workqueue") or {}).get("token") or ""
        worker = _spawn(
            f"{queue}-worker",
            [_worker_python(queue), "-m", "worker.agent", "--server", "http://localhost:8000",
             "--token", token, "--queue", queue,
             "--target", f"http://localhost:{leg['port']}"],
            str(ROOT / "src"),
            {"SAFETY_MODEL_DIR": SAFETY_MODEL_DIR} if queue in ("image", "video") else {},
        )
        held = queue

    try:
        while not stopping["flag"]:
            pending = {q: _waiting(q) for q in QUEUES}
            for q in QUEUES:
                if pending[q] > 0:
                    idle_ticks[q] = 0
                else:
                    idle_ticks[q] += 1

            if any(pending.values()):
                all_idle_since = None
            elif all_idle_since is None:
                all_idle_since = time.time()

            if idle_exit is not None and all_idle_since is not None \
                    and time.time() - all_idle_since >= idle_exit:
                _log_auto(f"idle {idle_exit:.0f}s, nothing pending {pending} — exiting", auto_log)
                break

            waits = {q: _oldest_wait(q) for q in QUEUES}
            action = choose_action(held, pending, idle_ticks, IDLE_TICKS, waits)

            if action != held:
                _log_auto(f"switch {held!r} -> {action!r} pending={pending}", auto_log)
                _stop_current()
                if action is not None:
                    t0 = time.time()
                    _start(action)
                    _log_auto(f"[{action}] ready in {time.time() - t0:.0f}s", auto_log)

            if worker is not None and worker.poll() is not None:
                raise SystemExit(f"[{held}] worker exited — see {LOGS / f'{held}-worker.log'}")

            time.sleep(POLL_SECONDS)
    finally:
        _log_auto(f"stopping, held={held!r}", auto_log)
        _stop_current()
        auto_log.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("queues", nargs="+", help="llm, image, mesh, all, or auto")
    parser.add_argument("--keep", action="store_true",
                        help="leave the model server up after the queue empties")
    parser.add_argument("--ready-timeout", type=float, default=300.0)
    parser.add_argument("--idle-exit", type=float, default=None,
                        help="auto mode only: stop and exit after this many seconds with "
                             "nothing pending anywhere (default: never)")
    args = parser.parse_args()

    if args.queues == ["auto"]:
        if not _up("http://localhost:8000/docs") and not _up("http://localhost:8000/"):
            print("warning: nothing answering on :8000 — start `python run.py` first", flush=True)
        auto(args.idle_exit, args.ready_timeout)
        return 0

    queues = QUEUES if args.queues == ["all"] else args.queues
    if not _up("http://localhost:8000/docs") and not _up("http://localhost:8000/"):
        print("warning: nothing answering on :8000 — start `python run.py` first, or the "
              "worker has no queue to pull from", flush=True)
    for queue in queues:
        drain(queue, keep=args.keep and queue == queues[-1],
              ready_timeout=args.ready_timeout)
    return 0


if __name__ == "__main__":
    sys.exit(main())
