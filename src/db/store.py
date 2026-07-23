"""Platform datastore (sqlite) — games, builds, jobs, events, workers.

The authoritative index for everything the run dir can't answer cheaply: who owns which game,
lifecycle status, compute grants/spend, the append-only build event log, and (next) the
inference job queue + worker fleet. The run dir stays the source of truth for the spec and
build artifacts — rows here point at it, never duplicate it.

Plain parameterized SQL, short-lived connections, WAL. No sqlite-isms in the DML, so a future
Postgres port is DDL + driver work, not a rewrite.
"""

import json
import logging
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Dict, List, Optional

from config.settings_manager import settings_manager
from db.estimates import estimate_seconds

logger = logging.getLogger(__name__)


class InsufficientCompute(Exception):
    """A game's compute grant can't cover another job of this size. Carries the numbers so the
    caller can tell the user how short they are."""

    def __init__(self, game_id: str, remaining: float, needed: float):
        super().__init__(f"game {game_id} has {remaining:.0f}s of compute left, "
                         f"needs {needed:.0f}s")
        self.game_id = game_id
        self.remaining = remaining
        self.needed = needed


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
    est_seconds      REAL NOT NULL DEFAULT 0,
    exec_seconds     REAL,
    lease_expires_at REAL,
    metadata         TEXT,
    batch_id         TEXT,
    finalized_at     REAL,
    created_at       REAL NOT NULL,
    started_at       REAL,
    finished_at      REAL
);
CREATE INDEX IF NOT EXISTS idx_jobs_claim ON jobs(queue, status, created_at);
CREATE INDEX IF NOT EXISTS idx_jobs_build ON jobs(build_id);
CREATE INDEX IF NOT EXISTS idx_jobs_batch ON jobs(batch_id, status);

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
    return Path(settings_manager.get_settings()["data_dir"]).resolve() / "platform.db"


# Paths whose WAL mode + schema this process has already applied. Doing that per connection is
# both wasted DDL on every query and a correctness bug: switching journal_mode needs a lock the
# busy handler does NOT cover, so sqlite returns "database is locked" outright whenever another
# connection is mid-transaction — which, under the worker's claim poll, is most of the time.
_INITIALIZED: set = set()
_INIT_LOCK = threading.Lock()


@contextmanager
def _db(immediate: bool = False):
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=10000")
    with _INIT_LOCK:
        if str(path) not in _INITIALIZED:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(_SCHEMA)
            _INITIALIZED.add(str(path))
    if immediate:
        # A read-then-write decision needs the write lock held across BOTH halves. sqlite's
        # implicit transaction only starts at the INSERT, which would let two enqueues read the
        # same headroom and each take it.
        conn.execute("BEGIN IMMEDIATE")
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


def _remaining_locked(conn, game_id: str) -> float:
    row = conn.execute(
        "SELECT seconds_granted, seconds_used FROM games WHERE id = ?", (game_id,)).fetchone()
    if row is None:
        return 0.0
    reserved = conn.execute(
        "SELECT COALESCE(SUM(est_seconds), 0) AS s FROM jobs "
        "WHERE game_id = ? AND status IN ('pending', 'claimed')", (game_id,)).fetchone()["s"]
    return row["seconds_granted"] - row["seconds_used"] - reserved


def compute_remaining(game_id: str) -> float:
    """The game's grant minus its measured spend minus the ESTIMATES of everything it already has
    in flight. Reservations are what make this a budget rather than a rear-view mirror: a build
    enqueues far faster than workers complete, so seconds_used alone reads near-zero right up to
    the moment a hundred queued jobs land."""
    with _db() as conn:
        return _remaining_locked(conn, game_id)


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
def _insert_job_locked(conn, queue: str, payload: Dict, game_id: Optional[str],
                       build_id: Optional[str], model: Optional[str],
                       batch_id: Optional[str], metadata: Optional[Dict]) -> str:
    """Admit + insert one job on a connection that ALREADY holds the write lock. Raises
    InsufficientCompute. Shared by enqueue_job and the continuation a completion lands, so both
    reserve against the same headroom under the same lock."""
    est = estimate_seconds(queue)
    job_id = uuid.uuid4().hex[:16]
    if game_id is not None:
        remaining = _remaining_locked(conn, game_id)
        if remaining < est:
            raise InsufficientCompute(game_id, remaining, est)
    conn.execute(
        "INSERT INTO jobs (id, queue, game_id, build_id, status, payload, model, "
        "est_seconds, batch_id, metadata, created_at) "
        "VALUES (?, ?, ?, ?, 'pending', ?, ?, ?, ?, ?, ?)",
        (job_id, queue, game_id, build_id, json.dumps(payload, ensure_ascii=False), model, est,
         batch_id, json.dumps(metadata, ensure_ascii=False) if metadata else None, time.time()),
    )
    return job_id


def enqueue_job(queue: str, payload: Dict, game_id: Optional[str] = None,
                build_id: Optional[str] = None, model: Optional[str] = None,
                batch_id: Optional[str] = None, metadata: Optional[Dict] = None) -> str:
    """Land a job as pending, ADMITTING it against its game's compute budget first. The check and
    the insert share one write transaction, so concurrent enqueues (parallel_fixes) can't each see
    the same headroom and all take it. Raises InsufficientCompute when the queue's estimate does
    not fit in what's left.

    A job with no game — chat, spec drafting — is platform cost rather than a game's, so it is
    attributed to nobody and gated by nothing.

    `metadata` is control-plane only and never reaches a worker: it carries `then` (the follow-up
    job, this result's operations, the batch's finalize). `batch_id` groups a chain so the last
    completion of the batch can be identified."""
    with _db(immediate=game_id is not None) as conn:
        return _insert_job_locked(conn, queue, payload, game_id, build_id, model,
                                  batch_id, metadata)


def abandon_job(job_id: str, error: str) -> bool:
    """Give up on a job whose enqueuer stopped waiting. Releases its reservation — a row left
    pending forever would hold estimate seconds against the game that nothing will ever spend or
    refund. A worker that completes it afterwards is dropped, exactly like a lapsed lease."""
    with _db() as conn:
        cur = conn.execute(
            "UPDATE jobs SET status = 'failed', error = ?, finished_at = ? "
            "WHERE id = ? AND status IN ('pending', 'claimed')",
            (error, time.time(), job_id))
    return cur.rowcount == 1


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
                 exec_seconds: float, gpu_type: Optional[str] = None,
                 continuation: Optional[Dict] = None) -> Optional[Dict]:
    """Land a job's outcome, debit its game's compute budget, and advance its chain. One
    transaction: the job row, the games seconds_used debit, the worker's busy-seconds and the
    follow-up job all move together. None if the job isn't this worker's claim (lease lapsed —
    the retry's result wins, this one is dropped).

    ONLY DELIVERED WORK IS BILLED. A game is debited when it got a result and never otherwise: a
    failed job, a lapsed-lease duplicate, and a job whose enqueuer abandoned it all leave
    seconds_used untouched. The worker's busy_seconds still moves in every case — that measures
    the GPU time WE pay for, which is real whether or not the user got anything for it.

    `continuation` is a fully-built {queue, payload, metadata?, model?} the caller derived from
    this job's `then`; it inherits game_id/build_id/batch_id so its seconds debit the same game
    even though nothing enqueued it inside a run_scope. Refused by the budget it is simply
    dropped — the batch finishes short and the finalize still runs, which is the asset stage's
    soft-degrade.

    Returns {batch_id, batch_complete, metadata, game_id, continuation_id}. batch_complete is
    true for exactly ONE completion per batch: the count of jobs still pending/claimed is taken
    AFTER the continuation is inserted, inside the same write transaction, so a completion can
    never see an empty batch whose next job simply doesn't exist yet."""
    now = time.time()
    status = "failed" if error else "done"
    # No immediate=True: the UPDATE below is the first statement, so the write lock is already
    # held by the time the continuation's admission reads the game's headroom.
    with _db() as conn:
        cur = conn.execute(
            "UPDATE jobs SET status = ?, result = ?, error = ?, exec_seconds = ?, gpu_type = ?, "
            "finished_at = ? WHERE id = ? AND worker_id = ? AND status = 'claimed'",
            (status, json.dumps(result, ensure_ascii=False) if result is not None else None,
             error, exec_seconds, gpu_type, now, job_id, worker_id),
        )
        if cur.rowcount != 1:
            return None
        row = conn.execute(
            "SELECT game_id, build_id, batch_id, metadata FROM jobs WHERE id = ?",
            (job_id,)).fetchone()
        if error is None and row["game_id"]:
            conn.execute(
                "UPDATE games SET seconds_used = seconds_used + ?, updated_at = ? WHERE id = ?",
                (exec_seconds, now, row["game_id"]))
        conn.execute(
            "UPDATE workers SET busy_seconds = busy_seconds + ?, last_seen_at = ? WHERE id = ?",
            (exec_seconds, now, worker_id))

        continuation_id = None
        if continuation is not None and error is None:
            try:
                continuation_id = _insert_job_locked(
                    conn, continuation["queue"], continuation["payload"], row["game_id"],
                    row["build_id"], continuation.get("model"), row["batch_id"],
                    continuation.get("metadata"))
            except InsufficientCompute as e:
                logger.error("continuation for job %s refused: %s", job_id, e)

        batch_complete = False
        if row["batch_id"]:
            left = conn.execute(
                "SELECT COUNT(*) AS n FROM jobs WHERE batch_id = ? "
                "AND status IN ('pending', 'claimed')", (row["batch_id"],)).fetchone()["n"]
            batch_complete = left == 0
    return {"batch_id": row["batch_id"], "batch_complete": batch_complete,
            "metadata": json.loads(row["metadata"]) if row["metadata"] else {},
            "game_id": row["game_id"], "continuation_id": continuation_id}


def get_job(job_id: str) -> Optional[Dict]:
    with _db() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if row is None:
        return None
    return _job_dict(row)


def _job_dict(row: sqlite3.Row) -> Dict:
    job = dict(row)
    job["payload"] = json.loads(job["payload"]) if job["payload"] else {}
    job["result"] = json.loads(job["result"]) if job["result"] else None
    job["metadata"] = json.loads(job["metadata"]) if job["metadata"] else {}
    return job


def claim_batch_finalize(batch_id: str) -> bool:
    """Take ownership of a batch's finalize. True for exactly one caller — the live completion
    that saw the batch empty, or the reaper picking up after a restart dropped it, never both."""
    with _db() as conn:
        cur = conn.execute(
            "UPDATE jobs SET finalized_at = ? WHERE batch_id = ? AND finalized_at IS NULL",
            (time.time(), batch_id))
    return cur.rowcount > 0


def has_active_batch(game_id: str) -> bool:
    """Whether this game has batched work still in the queue. The asset stage outlives the thread
    that started it, so this is what a second skin request has to check."""
    with _db() as conn:
        row = conn.execute(
            "SELECT 1 FROM jobs WHERE game_id = ? AND batch_id IS NOT NULL "
            "AND status IN ('pending', 'claimed') LIMIT 1", (game_id,)).fetchone()
    return row is not None


def batch_jobs(batch_id: str) -> List[Dict]:
    with _db() as conn:
        rows = conn.execute(
            "SELECT * FROM jobs WHERE batch_id = ? ORDER BY created_at", (batch_id,)).fetchall()
    return [_job_dict(r) for r in rows]


# ── reaper sweeps ─────────────────────────────────────────────────────────────
def requeue_lapsed_leases() -> int:
    """Return claimed jobs whose worker stopped heartbeating to pending. claim_job does this too,
    but only when a claim arrives — a queue that goes quiet would otherwise hold a dead job (and
    its reservation) forever."""
    with _db() as conn:
        cur = conn.execute(
            "UPDATE jobs SET status = 'pending', worker_id = NULL, lease_expires_at = NULL "
            "WHERE status = 'claimed' AND lease_expires_at < ?", (time.time(),))
    return cur.rowcount


def fail_stale_pending(max_age_seconds: float) -> List[Dict]:
    """Fail jobs nobody ever claimed, releasing their reservations. For chained jobs this replaces
    the enqueuer timeout: no clock starts until a job is real work."""
    cutoff = time.time() - max_age_seconds
    with _db() as conn:
        rows = conn.execute(
            "SELECT id, queue, batch_id, game_id FROM jobs "
            "WHERE status = 'pending' AND created_at < ?", (cutoff,)).fetchall()
        if rows:
            conn.execute(
                "UPDATE jobs SET status = 'failed', error = ?, finished_at = ? "
                "WHERE status = 'pending' AND created_at < ?",
                (f"pending longer than {max_age_seconds:.0f}s with no worker", time.time(),
                 cutoff))
    return [dict(r) for r in rows]


def batches_awaiting_finalize(grace_seconds: float) -> List[str]:
    """Batches whose jobs are all terminal but whose finalize never ran — the live completion was
    lost to a restart. Grace-delayed so a finalize in flight is not duplicated."""
    cutoff = time.time() - grace_seconds
    with _db() as conn:
        rows = conn.execute(
            "SELECT batch_id FROM jobs WHERE batch_id IS NOT NULL AND finalized_at IS NULL "
            "GROUP BY batch_id "
            "HAVING SUM(CASE WHEN status IN ('pending', 'claimed') THEN 1 ELSE 0 END) = 0 "
            "   AND MAX(finished_at) < ?", (cutoff,)).fetchall()
    return [r["batch_id"] for r in rows]


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


def backlog_seconds(queue: str) -> float:
    """Projected GPU-seconds still owed to clear a queue: the reserved estimate of every job not
    yet finished (pending + claimed). What the admin view reads as the live backlog cost."""
    with _db() as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(est_seconds), 0) AS s FROM jobs "
            "WHERE queue = ? AND status IN ('pending', 'claimed')", (queue,)).fetchone()
    return row["s"]


def gpu_seconds(queue: str, since: Optional[float] = None) -> Dict:
    """Recorded GPU-seconds for FINISHED jobs on a queue, optionally bounded by finished_at.

    paid   = every finished job's exec_seconds (done AND failed) — the GPU time WE pay for, real
             whether or not the user got anything, mirroring workers.busy_seconds.
    billed = only delivered, game-attributed work (status done, game_id set) — what actually
             debited games.seconds_used.
    The gap between them is unbilled GPU we ate (failures, chat/spec platform jobs)."""
    clause = "AND finished_at >= ?" if since is not None else ""
    args = [queue] + ([since] if since is not None else [])
    with _db() as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(exec_seconds), 0) AS paid, "
            "COALESCE(SUM(CASE WHEN status = 'done' AND game_id IS NOT NULL "
            "THEN exec_seconds ELSE 0 END), 0) AS billed "
            f"FROM jobs WHERE queue = ? AND finished_at IS NOT NULL {clause}",
            args).fetchone()
    return {"paid": row["paid"], "billed": row["billed"]}


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
