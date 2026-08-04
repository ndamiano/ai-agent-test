"""Platform datastore (sqlite) — games, builds, jobs, events, workers.

The authoritative index for everything the run dir can't answer cheaply: who owns which game,
lifecycle status, compute grants/spend, the append-only event log (build/spec lifecycle rows
keyed by game_id, user-action analytics rows keyed by user_id — one table, disjoint on user_id),
the inference job queue and the worker fleet. The run dir stays the source of truth for the spec
and build artifacts — rows here point at it, never duplicate it.

Plain parameterized SQL, short-lived connections, WAL. No sqlite-isms in the DML, so a future
Postgres port is DDL + driver work, not a rewrite.
"""

import hashlib
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
    game_id    TEXT,
    user_id    TEXT,
    build_id   TEXT,
    kind       TEXT NOT NULL,
    payload    TEXT,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_game ON events(game_id, id);
CREATE INDEX IF NOT EXISTS idx_events_user ON events(user_id, created_at);

CREATE TABLE IF NOT EXISTS violations (
    id         INTEGER PRIMARY KEY,
    user_id    TEXT,
    game_id    TEXT,
    source     TEXT NOT NULL,
    category   TEXT NOT NULL,
    matched    TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_violations_user ON violations(user_id, created_at);

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
    # Not resolve_base_path(): the execution_context working dir is repointable per call, and a db
    # that followed it would fork an empty copy there — an enqueued job would land where no worker
    # is looking. data_dir, not working_directory: control-plane state is not artifact output.
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


def update_prompt_meta(game_id: str, title: str) -> None:
    """Mirror the prompt's title onto the game row and mark the run buildable."""
    with _db() as conn:
        conn.execute(
            "UPDATE games SET title = ?, status = ?, updated_at = ? WHERE id = ?",
            (title, "ready", time.time(), game_id),
        )


def set_status(game_id: str, status: str) -> None:
    with _db() as conn:
        conn.execute("UPDATE games SET status = ?, updated_at = ? WHERE id = ?",
                     (status, time.time(), game_id))


def charge_game(game_id: str, credits: int, seconds: float) -> None:
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
    the insert share one write transaction, so concurrent enqueues can't each see the same
    headroom and all take it. Raises InsufficientCompute when the queue's estimate does
    not fit in what's left.

    A job with no game is platform cost rather than a game's, so it is attributed to nobody and
    gated by nothing.

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


def abandon_build_jobs(build_id: str, error: str) -> int:
    """Fail a build's unfinished turns. A claimed one is left to its worker's completion, which
    finds the cursor done and stops there."""
    with _db() as conn:
        cur = conn.execute(
            "UPDATE jobs SET status = 'failed', error = ?, finished_at = ? "
            "WHERE build_id = ? AND status = 'pending'",
            (error, time.time(), build_id))
    return cur.rowcount


def cancel_pending_build_turn(build_id: str, error: str) -> int:
    """Fail the build's queued llm TURN and nothing else — a paused build keeps the art it already
    asked for, and that rides the same build_id. A claimed turn is left to its worker."""
    with _db() as conn:
        cur = conn.execute(
            "UPDATE jobs SET status = 'failed', error = ?, finished_at = ? "
            "WHERE build_id = ? AND status = 'pending' AND queue = 'llm' "
            "AND json_extract(metadata, '$.stage') = 'build'",
            (error, time.time(), build_id))
    return cur.rowcount


def abandon_pending_batch_jobs(game_id: str, error: str) -> int:
    """Fail a game's queued (still-unclaimed) batch jobs, releasing their reservations — the
    build-vs-assets budget priority: a build turn refused for headroom preempts the opportunistic
    asset renders rather than dying. Claimed jobs are left alone (their GPU time is already being
    paid for); a batch failed whole is finalized by the reaper's batches_awaiting_finalize sweep."""
    with _db() as conn:
        cur = conn.execute(
            "UPDATE jobs SET status = 'failed', error = ?, finished_at = ? "
            "WHERE game_id = ? AND batch_id IS NOT NULL AND status = 'pending'",
            (error, time.time(), game_id))
    return cur.rowcount


def queue_has_work(queue: str) -> bool:
    """Lock-free peek for the claim long-poll's scan ticks: a pending row, or a claimed one whose
    lease lapsed. Even a no-match UPDATE takes the WAL writer lock, so idle workers scanning at
    50ms must read, not attempt to claim. The peek→claim race is harmless — the claim UPDATE
    stays atomic, so two peekers resolve to one claimant."""
    now = time.time()
    with _db() as conn:
        row = conn.execute(
            "SELECT 1 FROM jobs WHERE queue = ? AND (status = 'pending' "
            "OR (status = 'claimed' AND lease_expires_at < ?)) LIMIT 1",
            (queue, now)).fetchone()
    return row is not None


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


def served_model(result: Optional[Dict]) -> Optional[str]:
    """The model the GPU actually served, off an llm result. The `model` column is written at
    ENQUEUE from what the caller asked for, which is a different string from what a worker's
    target had loaded — a pod serving DeepSeek answered 387 jobs filed as qwen. Local servers
    report the gguf path, hosted ones a name; the basename makes the two comparable."""
    name = (result or {}).get("model")
    return name.rsplit("/", 1)[-1] if isinstance(name, str) and name else None


def complete_job(job_id: str, worker_id: str, result: Optional[Dict], error: Optional[str],
                 exec_seconds: float, gpu_type: Optional[str] = None,
                 continuation: Optional[Dict] = None) -> Optional[Dict]:
    """Land a job's outcome, debit its game's compute budget, and advance its chain. One
    transaction: the job row, the games and builds seconds_used debits, the worker's busy-seconds
    and the follow-up job all move together. None if the job isn't this worker's claim (lease
    lapsed — the retry's result wins, this one is dropped).

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
            "model = COALESCE(?, model), "
            "finished_at = ? WHERE id = ? AND worker_id = ? AND status = 'claimed'",
            (status, json.dumps(result, ensure_ascii=False) if result is not None else None,
             error, exec_seconds, gpu_type, served_model(result), now, job_id, worker_id),
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
        if error is None and row["build_id"]:
            conn.execute(
                "UPDATE builds SET seconds_used = seconds_used + ? WHERE id = ?",
                (exec_seconds, row["build_id"]))
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
            "game_id": row["game_id"], "build_id": row["build_id"],
            "continuation_id": continuation_id}


def get_job(job_id: str) -> Optional[Dict]:
    with _db() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if row is None:
        return None
    return _job_dict(row)


def clear_job_body(job_id: str) -> None:
    """Drop one job's request and reply, keeping the row. Called only once the body is durably
    archived elsewhere — a build turn's payload is the whole transcript so far, so the rows are
    where a build's conversation gets stored once per turn (measured 2026-07-31: 951 MB)."""
    with _db() as conn:
        conn.execute("UPDATE jobs SET payload = NULL, result = NULL WHERE id = ?", (job_id,))


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
    that started it, so this is what a second asset request has to check."""
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


# The prompt log's index columns. A listing carries only sizes plus the head of the system prompt,
# and the reader pulls one turn's full text at a time. A build turn's body has moved OUT of the row
# by the time it is read (maestro/codegen/turn_log.py), which is what the NULLs here mean — the
# router fills those rows in from the run dir's log.
# A db that has served both wire formats holds both, and a chat body's system prompt is messages[0]
# — MessageBuilder.build is what puts it there.
_TURN_COLUMNS = (
    "id, game_id, build_id, status, model, created_at, started_at, finished_at, "
    "exec_seconds, error, metadata, length(payload) AS payload_chars, "
    "COALESCE(json_extract(payload, '$.body.instructions'), "
    "         json_extract(payload, '$.body.messages[0].content')) AS system, "
    "COALESCE(json_array_length(json_extract(payload, '$.body.input')), "
    "         json_array_length(json_extract(payload, '$.body.messages')) - 1) AS n_messages"
)

_SYSTEM_HEAD_CHARS = 160


def system_index(system: Optional[str]) -> Dict:
    """A turn's system prompt as index columns. A turn's system prompt is its prompt FILE rendered
    — measured over a 661-turn build, 8 distinct texts covered every turn. Hashing it is what lets
    the reader collapse a log into the handful of prompts that actually produced it; the file name
    itself is never recorded."""
    system = system or ""
    return {"system_hash": hashlib.sha1(system.encode("utf-8")).hexdigest()[:12],
            "system_head": system[:_SYSTEM_HEAD_CHARS],
            "system_chars": len(system)}


def _llm_turns(where: str, params: tuple, limit: int) -> List[Dict]:
    with _db() as conn:
        rows = conn.execute(
            f"SELECT {_TURN_COLUMNS} FROM jobs WHERE queue = 'llm' AND {where} "
            # Newest-first under the cap, reversed after: a log past the cap loses its OLDEST
            # turns, never the ones the reader came for.
            "ORDER BY created_at DESC, rowid DESC LIMIT ?", params + (limit,)).fetchall()
    out = []
    for row in reversed(rows):
        turn = dict(row)
        turn.update(system_index(turn.pop("system")))
        turn["metadata"] = json.loads(turn["metadata"]) if turn["metadata"] else {}
        out.append(turn)
    return out


def llm_turns_for_game(game_id: str, limit: int = 2000) -> List[Dict]:
    """Every llm turn one game spent, oldest first."""
    return _llm_turns("game_id = ?", (game_id,), limit)


def llm_turns_platform(limit: int = 2000) -> List[Dict]:
    """The turns no game owns — enqueued with no game to bill. Nothing else records them."""
    return _llm_turns("game_id IS NULL", (), limit)


def llm_turns_all(limit: int = 2000) -> List[Dict]:
    return _llm_turns("1 = 1", (), limit)


def llm_turn_buckets() -> List[Dict]:
    """One row per game that has spent llm turns (plus a game_id=None row for the platform's own),
    newest activity first — the pick list for the prompt log."""
    with _db() as conn:
        rows = conn.execute(
            "SELECT j.game_id AS game_id, COUNT(*) AS turns, MIN(j.created_at) AS first_at, "
            "MAX(j.created_at) AS last_at, SUM(COALESCE(j.exec_seconds, 0)) AS exec_seconds, "
            "g.title AS title, g.mode AS mode, g.status AS status, g.user_id AS user_id "
            "FROM jobs j LEFT JOIN games g ON g.id = j.game_id "
            "WHERE j.queue = 'llm' GROUP BY j.game_id ORDER BY last_at DESC").fetchall()
    return [dict(r) for r in rows]


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


def stuck_builds(grace_seconds: float) -> List[str]:
    """Games still marked 'building' that have NO build llm turn in flight — their driver process
    died between a completion and the next enqueue, or the turn failed as stale-pending so no
    /worker/complete ever fired to advance the chain. The reaper re-advances them from the durable
    cursor. Grace-delayed off the newest terminal build turn so a live (slow) advance — which holds
    the per-run advance lock anyway — is never mistaken for a dead one."""
    cutoff = time.time() - grace_seconds
    with _db() as conn:
        rows = conn.execute(
            "SELECT g.id FROM games g WHERE g.status = 'building' "
            "AND NOT EXISTS (SELECT 1 FROM jobs j WHERE j.game_id = g.id "
            "  AND j.status IN ('pending', 'claimed') "
            "  AND json_extract(j.metadata, '$.stage') = 'build') "
            "AND (SELECT MAX(j2.finished_at) FROM jobs j2 WHERE j2.game_id = g.id "
            "     AND json_extract(j2.metadata, '$.stage') = 'build') < ?",
            (cutoff,)).fetchall()
    return [r["id"] for r in rows]


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
    The gap between them is unbilled GPU we ate (failures, and jobs no game owns)."""
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


def jobs_finished_totals(since: Optional[float] = None) -> Dict:
    """Finished-job counts and exec time by outcome, all queues — the cost panel's our-side half."""
    clause = "AND finished_at >= ?" if since is not None else ""
    args = [since] if since is not None else []
    out = {"done": {"n": 0, "exec_seconds": 0.0}, "failed": {"n": 0, "exec_seconds": 0.0}}
    with _db() as conn:
        for row in conn.execute(
                "SELECT status, COUNT(*) AS n, COALESCE(SUM(exec_seconds), 0) AS s "
                f"FROM jobs WHERE finished_at IS NOT NULL {clause} GROUP BY status", args):
            if row["status"] in out:
                out[row["status"]] = {"n": row["n"], "exec_seconds": row["s"]}
    return out


def workers_since(since: float) -> List[Dict]:
    """Worker rows alive at any point after `since` — for wall-clock and ghost accounting."""
    with _db() as conn:
        rows = conn.execute(
            "SELECT id, queue, gpu_type, source, pod_id, started_at, last_seen_at, terminated_at "
            "FROM workers WHERE COALESCE(terminated_at, last_seen_at) >= ? OR terminated_at IS NULL",
            (since,)).fetchall()
    return [dict(r) for r in rows]


def record_event(game_id: str, kind: str, payload: Dict, build_id: Optional[str] = None) -> None:
    with _db() as conn:
        conn.execute(
            "INSERT INTO events (game_id, build_id, kind, payload, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (game_id, build_id, kind, json.dumps(payload, ensure_ascii=False, default=str),
             time.time()),
        )


def events_for(game_id: str, after_id: int = 0, limit: int = 500) -> List[Dict]:
    # user_id IS NULL: the build/spec lifecycle log only. A user-action row (analytics) may carry
    # the same game_id, and the replay path would misread its kinds as lifecycle events.
    with _db() as conn:
        rows = conn.execute(
            "SELECT * FROM events WHERE game_id = ? AND user_id IS NULL AND id > ? "
            "ORDER BY id LIMIT ?",
            (game_id, after_id, limit),
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["payload"] = json.loads(d["payload"]) if d["payload"] else {}
        out.append(d)
    return out


def record_violation(user_id: Optional[str], game_id: Optional[str], source: str,
                     category: str, matched: str) -> None:
    """One safety refusal, durable and keyed to the user — what the admin panel reads to see a
    repeat offender. Carries only the matched term(s), never the flagged text."""
    with _db() as conn:
        conn.execute(
            "INSERT INTO violations (user_id, game_id, source, category, matched, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (user_id, game_id, source, category, matched, time.time()),
        )


def list_violations(limit: int = 200) -> List[Dict]:
    with _db() as conn:
        rows = conn.execute(
            "SELECT * FROM violations ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]


def record_user_events(user_id: str, rows: List[Dict]) -> None:
    """Batch-insert user-action analytics rows: {kind, payload, game_id?, created_at}."""
    with _db() as conn:
        conn.executemany(
            "INSERT INTO events (game_id, user_id, kind, payload, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            [(r.get("game_id"), user_id, r["kind"],
              json.dumps(r.get("payload") or {}, ensure_ascii=False, default=str),
              r["created_at"]) for r in rows],
        )


def user_event_rollup(since: float) -> Dict[str, List[Dict]]:
    """Day-bucketed user-action counts: events per (day, kind) and distinct users per day."""
    with _db() as conn:
        kinds = conn.execute(
            "SELECT date(created_at, 'unixepoch') AS day, kind, COUNT(*) AS n "
            "FROM events WHERE user_id IS NOT NULL AND created_at >= ? "
            "GROUP BY day, kind ORDER BY day",
            (since,)).fetchall()
        users = conn.execute(
            "SELECT date(created_at, 'unixepoch') AS day, COUNT(DISTINCT user_id) AS n "
            "FROM events WHERE user_id IS NOT NULL AND created_at >= ? "
            "GROUP BY day ORDER BY day",
            (since,)).fetchall()
    return {"kinds": [dict(r) for r in kinds], "users": [dict(r) for r in users]}
