"""Platform datastore (sqlite) — games, builds, jobs, events, workers.

The authoritative index for everything the run dir can't answer cheaply: who owns which game,
lifecycle status, compute grants/spend, the append-only build event log, and (next) the
inference job queue + worker fleet. The run dir stays the source of truth for the spec and
build artifacts — rows here point at it, never duplicate it.

Plain parameterized SQL, short-lived connections, WAL. No sqlite-isms in the DML, so a future
Postgres port is DDL + driver work, not a rewrite.
"""

import json
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Dict, List, Optional

_SCHEMA = """
CREATE TABLE IF NOT EXISTS games (
    id              TEXT PRIMARY KEY,
    user_id         TEXT NOT NULL,
    title           TEXT NOT NULL DEFAULT '',
    mode            TEXT NOT NULL DEFAULT '',
    status          TEXT NOT NULL DEFAULT 'draft',
    credits_spent   INTEGER NOT NULL DEFAULT 0,
    seconds_granted REAL NOT NULL DEFAULT 0,
    seconds_used    REAL NOT NULL DEFAULT 0,
    created_at      REAL NOT NULL,
    updated_at      REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_games_user ON games(user_id, created_at);

CREATE TABLE IF NOT EXISTS builds (
    id           TEXT PRIMARY KEY,
    game_id      TEXT NOT NULL REFERENCES games(id),
    kind         TEXT NOT NULL DEFAULT 'build',
    status       TEXT NOT NULL DEFAULT 'queued',
    steps        INTEGER,
    seconds_used REAL NOT NULL DEFAULT 0,
    queued_at    REAL NOT NULL,
    started_at   REAL,
    finished_at  REAL
);
CREATE INDEX IF NOT EXISTS idx_builds_game ON builds(game_id, queued_at);

CREATE TABLE IF NOT EXISTS jobs (
    id               TEXT PRIMARY KEY,
    queue            TEXT NOT NULL,
    game_id          TEXT,
    build_id         TEXT,
    status           TEXT NOT NULL DEFAULT 'pending',
    payload          TEXT,
    result           TEXT,
    error            TEXT,
    worker_id        TEXT,
    model            TEXT,
    gpu_type         TEXT,
    exec_seconds     REAL,
    lease_expires_at REAL,
    created_at       REAL NOT NULL,
    started_at       REAL,
    finished_at      REAL
);
CREATE INDEX IF NOT EXISTS idx_jobs_claim ON jobs(queue, status, created_at);
CREATE INDEX IF NOT EXISTS idx_jobs_build ON jobs(build_id);

CREATE TABLE IF NOT EXISTS events (
    id         INTEGER PRIMARY KEY,
    game_id    TEXT NOT NULL,
    build_id   TEXT,
    kind       TEXT NOT NULL,
    payload    TEXT,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_game ON events(game_id, id);

CREATE TABLE IF NOT EXISTS workers (
    id            TEXT PRIMARY KEY,
    queue         TEXT,
    gpu_type      TEXT,
    source        TEXT,
    pod_id        TEXT,
    busy_seconds  REAL NOT NULL DEFAULT 0,
    started_at    REAL NOT NULL,
    last_seen_at  REAL,
    terminated_at REAL
);
"""


def _db_path() -> Path:
    # Not resolve_base_path(): tools repoint the execution_context working dir (the reskin points it
    # at game/assets/ for ComfyUI), which would fork an empty db there — an enqueued job would land
    # where no worker is looking. data_dir, not working_directory: control-plane state does not
    # belong in the artifact output tree.
    from config.settings_manager import settings_manager
    return Path(settings_manager.get_settings()["data_dir"]).resolve() / "platform.db"


# Paths whose WAL mode + schema this process has already applied. Doing that per connection is
# both wasted DDL on every query and a correctness bug: switching journal_mode needs a lock the
# busy handler does NOT cover, so sqlite returns "database is locked" outright whenever another
# connection is mid-transaction — which, under the worker's claim poll, is most of the time.
_INITIALIZED: set = set()
_INIT_LOCK = threading.Lock()


@contextmanager
def _db():
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=10000")
    with _INIT_LOCK:
        if str(path) not in _INITIALIZED:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(_SCHEMA)
            # No migration mechanism exists; a db created before the pod_id column needs it added.
            cols = {r["name"] for r in conn.execute("PRAGMA table_info(workers)")}
            if "pod_id" not in cols:
                conn.execute("ALTER TABLE workers ADD COLUMN pod_id TEXT")
            _INITIALIZED.add(str(path))
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _row_dict(row: Optional[sqlite3.Row]) -> Optional[Dict]:
    return dict(row) if row is not None else None


# ── games ─────────────────────────────────────────────────────────────────────
def create_game(game_id: str, user_id: str) -> None:
    now = time.time()
    with _db() as conn:
        conn.execute(
            "INSERT INTO games (id, user_id, created_at, updated_at) VALUES (?, ?, ?, ?)",
            (game_id, user_id, now, now),
        )


def game(game_id: str) -> Optional[Dict]:
    with _db() as conn:
        return _row_dict(conn.execute("SELECT * FROM games WHERE id = ?", (game_id,)).fetchone())


def owner_of(game_id: str) -> Optional[str]:
    with _db() as conn:
        row = conn.execute("SELECT user_id FROM games WHERE id = ?", (game_id,)).fetchone()
    return row["user_id"] if row else None


def list_games(user_id: str) -> List[Dict]:
    with _db() as conn:
        rows = conn.execute(
            "SELECT * FROM games WHERE user_id = ? ORDER BY created_at DESC", (user_id,)
        ).fetchall()
    return [dict(r) for r in rows]


def update_spec_meta(game_id: str, title: str, mode: str, frozen: bool) -> None:
    """Mirror the spec's identity fields onto the game row when the spec is (re)written."""
    with _db() as conn:
        conn.execute(
            "UPDATE games SET title = ?, mode = ?, status = ?, updated_at = ? WHERE id = ?",
            (title, mode, "frozen" if frozen else "draft", time.time(), game_id),
        )


def set_status(game_id: str, status: str) -> None:
    with _db() as conn:
        conn.execute("UPDATE games SET status = ?, updated_at = ? WHERE id = ?",
                     (status, time.time(), game_id))


def charge_game(game_id: str, credits: int, seconds: float) -> None:
    """Record a credit spend against this game and grant its compute budget."""
    with _db() as conn:
        conn.execute(
            "UPDATE games SET credits_spent = credits_spent + ?, "
            "seconds_granted = seconds_granted + ?, updated_at = ? WHERE id = ?",
            (credits, seconds, time.time(), game_id),
        )


def is_charged(game_id: str) -> bool:
    """True once this game has been charged for a build. The charge is per-game and idempotent:
    a build enqueued for an already-charged game (re-trigger, resume-after-crash) is never
    deducted again — charged stays charged as long as the run can eventually finish."""
    with _db() as conn:
        row = conn.execute("SELECT credits_spent FROM games WHERE id = ?", (game_id,)).fetchone()
    return bool(row and row["credits_spent"] > 0)


def add_seconds_used(game_id: str, seconds: float) -> None:
    with _db() as conn:
        conn.execute(
            "UPDATE games SET seconds_used = seconds_used + ?, updated_at = ? WHERE id = ?",
            (seconds, time.time(), game_id),
        )


# ── builds ────────────────────────────────────────────────────────────────────
def create_build(game_id: str, kind: str = "build") -> str:
    build_id = uuid.uuid4().hex[:12]
    with _db() as conn:
        conn.execute(
            "INSERT INTO builds (id, game_id, kind, status, queued_at) VALUES (?, ?, ?, ?, ?)",
            (build_id, game_id, kind, "queued", time.time()),
        )
    return build_id


def build_started(build_id: str) -> None:
    with _db() as conn:
        conn.execute("UPDATE builds SET status = 'running', started_at = ? WHERE id = ?",
                     (time.time(), build_id))


def build_finished(build_id: str, status: str, steps: Optional[int] = None) -> None:
    with _db() as conn:
        conn.execute(
            "UPDATE builds SET status = ?, steps = ?, finished_at = ? WHERE id = ?",
            (status, steps, time.time(), build_id),
        )


def builds_for(game_id: str) -> List[Dict]:
    with _db() as conn:
        rows = conn.execute(
            "SELECT * FROM builds WHERE game_id = ? ORDER BY queued_at", (game_id,)
        ).fetchall()
    return [dict(r) for r in rows]


# ── jobs (the worker-pull inference queue) ────────────────────────────────────
# Producers (the build loop, in-process) enqueue; workers claim over HTTP. A claim is a single
# atomic UPDATE...RETURNING, so two workers can never take the same job. A claimed job whose
# lease lapses (worker died mid-inference) returns to pending on the next claim sweep — the
# enqueuer just keeps waiting and the retry is invisible to it.
def enqueue_job(queue: str, payload: Dict, game_id: Optional[str] = None,
                build_id: Optional[str] = None, model: Optional[str] = None) -> str:
    job_id = uuid.uuid4().hex[:16]
    with _db() as conn:
        conn.execute(
            "INSERT INTO jobs (id, queue, game_id, build_id, status, payload, model, created_at) "
            "VALUES (?, ?, ?, ?, 'pending', ?, ?, ?)",
            (job_id, queue, game_id, build_id,
             json.dumps(payload, ensure_ascii=False), model, time.time()),
        )
    return job_id


def claim_job(queue: str, worker_id: str, lease_seconds: float) -> Optional[Dict]:
    """Atomically claim the oldest pending job on `queue` (requeueing expired leases first).
    Returns the job dict with a decoded payload, or None if the queue is empty."""
    now = time.time()
    with _db() as conn:
        conn.execute(
            "UPDATE jobs SET status = 'pending', worker_id = NULL, lease_expires_at = NULL "
            "WHERE status = 'claimed' AND lease_expires_at < ?", (now,))
        row = conn.execute(
            "UPDATE jobs SET status = 'claimed', worker_id = ?, started_at = ?, "
            "lease_expires_at = ? WHERE id = ("
            "  SELECT id FROM jobs WHERE queue = ? AND status = 'pending' "
            "  ORDER BY created_at LIMIT 1) "
            "RETURNING *",
            (worker_id, now, now + lease_seconds, queue),
        ).fetchone()
    if row is None:
        return None
    job = dict(row)
    job["payload"] = json.loads(job["payload"]) if job["payload"] else {}
    return job


def heartbeat_job(job_id: str, worker_id: str, lease_seconds: float) -> bool:
    """Extend a claimed job's lease. False if the job isn't this worker's claim any more
    (lease already lapsed and someone else took it) — the worker should drop the job."""
    with _db() as conn:
        cur = conn.execute(
            "UPDATE jobs SET lease_expires_at = ? "
            "WHERE id = ? AND worker_id = ? AND status = 'claimed'",
            (time.time() + lease_seconds, job_id, worker_id),
        )
    return cur.rowcount == 1


def complete_job(job_id: str, worker_id: str, result: Optional[Dict], error: Optional[str],
                 exec_seconds: float, gpu_type: Optional[str] = None) -> bool:
    """Land a job's outcome and debit its game's compute budget. One transaction: the job row,
    the games seconds_used debit, and the worker's busy-seconds all move together. False if the
    job isn't this worker's claim (lease lapsed — the retry's result wins, this one is dropped)."""
    now = time.time()
    status = "failed" if error else "done"
    with _db() as conn:
        cur = conn.execute(
            "UPDATE jobs SET status = ?, result = ?, error = ?, exec_seconds = ?, gpu_type = ?, "
            "finished_at = ? WHERE id = ? AND worker_id = ? AND status = 'claimed'",
            (status, json.dumps(result, ensure_ascii=False) if result is not None else None,
             error, exec_seconds, gpu_type, now, job_id, worker_id),
        )
        if cur.rowcount != 1:
            return False
        row = conn.execute("SELECT game_id FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if row and row["game_id"]:
            conn.execute(
                "UPDATE games SET seconds_used = seconds_used + ?, updated_at = ? WHERE id = ?",
                (exec_seconds, now, row["game_id"]))
        conn.execute(
            "UPDATE workers SET busy_seconds = busy_seconds + ?, last_seen_at = ? WHERE id = ?",
            (exec_seconds, now, worker_id))
    return True


def get_job(job_id: str) -> Optional[Dict]:
    with _db() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if row is None:
        return None
    job = dict(row)
    job["payload"] = json.loads(job["payload"]) if job["payload"] else {}
    job["result"] = json.loads(job["result"]) if job["result"] else None
    return job


# ── workers (fleet + utilization facts) ───────────────────────────────────────
def worker_seen(worker_id: str, queue: str, gpu_type: Optional[str] = None,
                source: Optional[str] = None, pod_id: Optional[str] = None) -> None:
    now = time.time()
    with _db() as conn:
        # terminated_at is cleared on re-register: RunPod restarts an exited container, and a
        # restarted worker that still looked terminated would be reaped mid-work.
        cur = conn.execute(
            "UPDATE workers SET queue = ?, last_seen_at = ?, pod_id = COALESCE(?, pod_id), "
            "terminated_at = NULL WHERE id = ?",
            (queue, now, pod_id, worker_id))
        if cur.rowcount == 0:
            conn.execute(
                "INSERT INTO workers (id, queue, gpu_type, source, pod_id, started_at, last_seen_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (worker_id, queue, gpu_type, source, pod_id, now, now))


def touch_worker(worker_id: str) -> None:
    with _db() as conn:
        conn.execute("UPDATE workers SET last_seen_at = ? WHERE id = ?",
                     (time.time(), worker_id))


def set_worker_terminated(worker_id: str) -> None:
    with _db() as conn:
        conn.execute("UPDATE workers SET terminated_at = ? WHERE id = ?",
                     (time.time(), worker_id))


def live_workers(queue: str, freshness_seconds: float) -> List[Dict]:
    with _db() as conn:
        rows = conn.execute(
            "SELECT * FROM workers WHERE queue = ? AND terminated_at IS NULL "
            "AND last_seen_at >= ?",
            (queue, time.time() - freshness_seconds)).fetchall()
    return [dict(r) for r in rows]


def stale_workers(queue: str, staleness_seconds: float) -> List[Dict]:
    """Pod-backed workers that stopped checking in without deregistering (crashed or wedged).
    Home-box workers (pod_id NULL) are never anyone's to reap."""
    with _db() as conn:
        rows = conn.execute(
            "SELECT * FROM workers WHERE queue = ? AND terminated_at IS NULL "
            "AND pod_id IS NOT NULL AND last_seen_at < ?",
            (queue, time.time() - staleness_seconds)).fetchall()
    return [dict(r) for r in rows]


def terminated_workers_with_pods(queue: str) -> List[Dict]:
    with _db() as conn:
        rows = conn.execute(
            "SELECT * FROM workers WHERE queue = ? AND terminated_at IS NOT NULL "
            "AND pod_id IS NOT NULL", (queue,)).fetchall()
    return [dict(r) for r in rows]


def queue_stats(queue: str) -> Dict:
    now = time.time()
    with _db() as conn:
        row = conn.execute(
            "SELECT SUM(CASE WHEN status = 'pending' THEN 1 ELSE 0 END) AS pending, "
            "SUM(CASE WHEN status = 'claimed' THEN 1 ELSE 0 END) AS claimed, "
            "MIN(CASE WHEN status = 'pending' THEN created_at END) AS oldest "
            "FROM jobs WHERE queue = ? AND status IN ('pending', 'claimed')",
            (queue,)).fetchone()
    return {
        "pending": row["pending"] or 0,
        "claimed": row["claimed"] or 0,
        "oldest_pending_age_seconds": (now - row["oldest"]) if row["oldest"] else None,
    }


# ── events (append-only build/spec lifecycle log) ─────────────────────────────
def record_event(game_id: str, kind: str, payload: Dict, build_id: Optional[str] = None) -> None:
    with _db() as conn:
        conn.execute(
            "INSERT INTO events (game_id, build_id, kind, payload, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (game_id, build_id, kind, json.dumps(payload, ensure_ascii=False, default=str),
             time.time()),
        )


def events_for(game_id: str, after_id: int = 0, limit: int = 500) -> List[Dict]:
    with _db() as conn:
        rows = conn.execute(
            "SELECT * FROM events WHERE game_id = ? AND id > ? ORDER BY id LIMIT ?",
            (game_id, after_id, limit),
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["payload"] = json.loads(d["payload"]) if d["payload"] else {}
        out.append(d)
    return out
