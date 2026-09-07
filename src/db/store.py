"""Platform datastore (sqlite) — games, builds, jobs, events, workers, pod refusals.

The authoritative index for everything the run dir can't answer cheaply: who owns which game,
lifecycle status, compute grants/spend, the append-only event log (build/spec lifecycle rows
keyed by game_id, user-action analytics rows keyed by user_id — one table, disjoint on user_id),
the inference job queue and the worker fleet. The run dir stays the source of truth for the spec
and build artifacts — rows here point at it, never duplicate it.

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
from db.estimates import estimate_seconds, gpu_rate
from tools.version import maestro_rev

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


class BuildEnded(Exception):
    """The job's build was stopped by hand. A thread still working for it (a world's later legs,
    a batch's retry) learns here that nothing it enqueues will be wanted."""


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
    finished_at  REAL,
    maestro_rev  TEXT
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
    billed_seconds   REAL,
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
    usd_per_hour  REAL,
    started_at    REAL NOT NULL,
    registered_at REAL,
    last_seen_at  REAL,
    terminated_at REAL
);

CREATE TABLE IF NOT EXISTS pod_refusals (
    id         INTEGER PRIMARY KEY,
    queue      TEXT NOT NULL,
    kind       TEXT NOT NULL,
    attempts   TEXT NOT NULL,
    error      TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_pod_refusals_queue ON pod_refusals(queue, kind, created_at);

CREATE TABLE IF NOT EXISTS pod_request_days (
    queue           TEXT NOT NULL,
    day             TEXT NOT NULL,
    attempts        INTEGER NOT NULL DEFAULT 0,
    stock_refusals  INTEGER NOT NULL DEFAULT 0,
    other_refusals  INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (queue, day)
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
            "INSERT INTO builds (id, game_id, kind, status, queued_at, maestro_rev) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (build_id, game_id, kind, "queued", time.time(), maestro_rev()),
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


def finish_open_builds(game_id: str, status: str) -> List[Dict]:
    """End every build of the game still queued or running, returning the rows ended."""
    now = time.time()
    with _db() as conn:
        rows = conn.execute(
            "UPDATE builds SET status = ?, finished_at = ? "
            "WHERE game_id = ? AND status IN ('queued', 'running') RETURNING id, kind",
            (status, now, game_id)).fetchall()
    return [dict(r) for r in rows]


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
        if build_id is None:
            # A job that belongs to a game belongs to a build: cost and history join on the build.
            raise ValueError(f"job on game {game_id!r} has no build_id")
        build = conn.execute("SELECT status FROM builds WHERE id = ?", (build_id,)).fetchone()
        if build is not None and build["status"] == "stopped":
            raise BuildEnded(f"build {build_id} of game {game_id} was stopped")
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


def _fail_locked(conn, where: str, params: tuple, error: str) -> int:
    """Fail every unfinished job matching `where`, leaving each row what a finished row keeps: the
    elided payload, never the request text. Rows are updated one at a time because the elision
    runs in Python, and the result is None on every one — a failed job delivered nothing."""
    rows = conn.execute(f"SELECT id, queue, payload FROM jobs WHERE {where}", params).fetchall()
    now = time.time()
    for row in rows:
        payload = elide_payload(row["queue"], json.loads(row["payload"] or "{}"))
        conn.execute(
            "UPDATE jobs SET status = 'failed', payload = ?, error = ?, finished_at = ? "
            "WHERE id = ?",
            (json.dumps(payload, ensure_ascii=False), error, now, row["id"]))
    return len(rows)


def abandon_job(job_id: str, error: str) -> bool:
    """Give up on a job whose enqueuer stopped waiting. Releases its reservation — a row left
    pending forever would hold estimate seconds against the game that nothing will ever spend or
    refund. A worker that completes it afterwards is dropped, exactly like a lapsed lease."""
    with _db() as conn:
        n = _fail_locked(conn, "id = ? AND status IN ('pending', 'claimed')", (job_id,), error)
    return n == 1


def abandon_game_jobs(game_id: str, error: str) -> int:
    """Fail everything the game has in flight on every queue, releasing the reservations. A
    CLAIMED job goes too: its worker may be the thing that hung, and a result it reports later
    is dropped exactly like a lapsed lease, so nothing the row chained to ever fires."""
    with _db() as conn:
        return _fail_locked(conn, "game_id = ? AND status IN ('pending', 'claimed')",
                            (game_id,), error)


def abandon_build_jobs(build_id: str, error: str) -> int:
    """Fail a build's unfinished turns. A claimed one is left to its worker's completion, which
    finds the cursor done and stops there."""
    with _db() as conn:
        return _fail_locked(conn, "build_id = ? AND status = 'pending'", (build_id,), error)


def cancel_pending_build_turn(build_id: str, error: str) -> int:
    """Fail the build's queued llm TURN and nothing else — a paused build keeps the art it already
    asked for, and that rides the same build_id. A claimed turn is left to its worker."""
    with _db() as conn:
        return _fail_locked(
            conn, "build_id = ? AND status = 'pending' AND queue = 'llm' "
            "AND json_extract(metadata, '$.stage') = 'build'", (build_id,), error)


def abandon_pending_batch_jobs(game_id: str, error: str) -> int:
    """Fail a game's queued (still-unclaimed) batch jobs, releasing their reservations — the
    build-vs-assets budget priority: a build turn refused for headroom preempts the opportunistic
    asset renders rather than dying. Claimed jobs are left alone (their GPU time is already being
    paid for); a batch failed whole is finalized by the reaper's batches_awaiting_finalize sweep."""
    with _db() as conn:
        return _fail_locked(
            conn, "game_id = ? AND batch_id IS NOT NULL AND status = 'pending'", (game_id,),
            error)


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


def _system_of(body: Dict) -> str:
    if body.get("instructions") is not None:
        return body["instructions"]
    first = (body.get("messages") or [{}])[0]
    return first.get("content") or "" if first.get("role") == "system" else ""


def _text_len(content) -> int:
    if isinstance(content, str):
        return len(content)
    return len(json.dumps(content, ensure_ascii=False)) if content else 0


def elide_payload(queue: str, payload: Dict) -> Dict:
    """What a job's request leaves on its row once it has run: the operational facts, never the
    text. An llm turn's body is the whole transcript so far and the run dir's turn log already
    holds it; an image or video body is a base64 blob the file store holds. Both wire formats
    (chat `messages`, Responses `input`) reduce to the same fields."""
    if queue == "llm":
        body = payload.get("body") or {}
        turns = body.get("messages") or body.get("input") or []
        system = _system_of(body)
        reasoning = body.get("reasoning")
        if isinstance(reasoning, dict):
            reasoning = reasoning.get("effort")
        return {"model": body.get("model"),
                "n_messages": len(turns) - (1 if body.get("messages") and system else 0),
                "prompt_chars": len(system) + sum(
                    _text_len(m.get("content") if "content" in m else m.get("output")
                              or m.get("arguments"))
                    for m in turns if m.get("role") != "system"),
                "reasoning": reasoning,
                "max_tokens": body.get("max_output_tokens") or body.get("max_tokens")}
    if queue == "image":
        wf = payload.get("workflow") or {}
        node = next((wf[k] for k in ("p", "6") if k in wf), {})
        return {"prompt": (node.get("inputs") or {}).get("text") or "",
                "mode": "img2img" if payload.get("uploads") else "txt2img"}
    if queue == "video":
        return {"kind": payload.get("kind"), "anims": list(payload.get("anims") or {}),
                "dirs": payload.get("dirs") or []}
    return {"kind": payload.get("kind")}


def elide_result(queue: str, result: Optional[Dict]) -> Optional[Dict]:
    """What a reply leaves on its row: token usage, why it stopped, which tools it called — never
    the text or the arguments. An art reply keeps everything but its base64 blobs — the blob
    paths, the served model and the safety verdict are the row's record of the render."""
    if result is None or "tool_names" in result:
        return result
    if queue != "llm":
        return {k: ([{kk: vv for kk, vv in i.items() if not kk.endswith("_b64")} for i in v]
                    if k == "images" else v)
                for k, v in result.items() if not k.endswith("_b64")}
    if "choices" in result:
        choice = (result.get("choices") or [{}])[0]
        calls = (choice.get("message") or {}).get("tool_calls") or []
        names = [(c.get("function") or {}).get("name") for c in calls]
        finish = choice.get("finish_reason")
    else:
        items = result.get("output") or []
        names = [i.get("name") for i in items if i.get("type") == "function_call"]
        finish = result.get("status")
    return {"usage": result.get("usage"), "finish_reason": finish,
            "tool_names": [n for n in names if n]}


def elide_job_result(job_id: str) -> None:
    """Called by whoever consumed a finished job's reply — the completion route for a chained job,
    `queue_client.run_job` for a blocking one — so the row holds the full reply only until it
    has been read."""
    with _db() as conn:
        row = conn.execute("SELECT queue, result FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None or not row["result"]:
            return
        elided = elide_result(row["queue"], json.loads(row["result"]))
        conn.execute("UPDATE jobs SET result = ? WHERE id = ?",
                     (json.dumps(elided, ensure_ascii=False), job_id))


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

    The debit is exec_seconds × the card's rate (`gpu_rate`): grants are 5090-seconds, and the
    row keeps both the raw exec_seconds (what the card ran) and billed_seconds (what the game
    paid), so the ledger survives a rate change.

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
    billed = exec_seconds * gpu_rate(gpu_type)
    # No immediate=True: the UPDATE below is the first statement, so the write lock is already
    # held by the time the continuation's admission reads the game's headroom.
    with _db() as conn:
        job_row = conn.execute(
            "SELECT queue, payload FROM jobs WHERE id = ? AND worker_id = ? AND status = 'claimed'",
            (job_id, worker_id)).fetchone()
        if job_row is None:
            return None
        payload = elide_payload(job_row["queue"], json.loads(job_row["payload"]))
        cur = conn.execute(
            "UPDATE jobs SET status = ?, payload = ?, result = ?, error = ?, exec_seconds = ?, "
            "billed_seconds = ?, gpu_type = ?, model = COALESCE(?, model), "
            "finished_at = ? WHERE id = ?",
            (status, json.dumps(payload, ensure_ascii=False),
             json.dumps(result, ensure_ascii=False) if result is not None else None,
             error, exec_seconds, billed, gpu_type, served_model(result), now, job_id),
        )
        if cur.rowcount != 1:
            return None
        row = conn.execute(
            "SELECT game_id, build_id, batch_id, metadata FROM jobs WHERE id = ?",
            (job_id,)).fetchone()
        if error is None and row["game_id"]:
            conn.execute(
                "UPDATE games SET seconds_used = seconds_used + ?, updated_at = ? WHERE id = ?",
                (billed, now, row["game_id"]))
        if error is None and row["build_id"]:
            conn.execute(
                "UPDATE builds SET seconds_used = seconds_used + ? WHERE id = ?",
                (billed, row["build_id"]))
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
        _fail_locked(conn, "status = 'pending' AND created_at < ?", (cutoff,),
                     f"pending longer than {max_age_seconds:.0f}s with no worker")
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


def worker_created(pod_id: str, queue: str, gpu_type: Optional[str],
                   usd_per_hour: Optional[float]) -> None:
    """A pod the scaler just created is a worker from that moment — billed, counted as capacity,
    shown as booting — keyed on the pod id its worker will register under. started_at is the
    create, so a boot is inside the row's life."""
    now = time.time()
    with _db() as conn:
        conn.execute(
            "INSERT INTO workers (id, queue, gpu_type, source, pod_id, usd_per_hour, started_at) "
            "VALUES (?, ?, ?, 'runpod', ?, ?, ?)",
            (pod_id, queue, gpu_type, pod_id, usd_per_hour, now))


def worker_seen(worker_id: str, queue: str, gpu_type: Optional[str] = None,
                source: Optional[str] = None, pod_id: Optional[str] = None) -> None:
    now = time.time()
    with _db() as conn:
        # terminated_at is cleared on re-register: RunPod restarts an exited container, and a
        # restarted worker that still looked terminated would be reaped mid-work. The card is the
        # worker's to report (nvidia-smi's name), over whatever the create guessed.
        cur = conn.execute(
            "UPDATE workers SET queue = ?, last_seen_at = ?, registered_at = COALESCE(registered_at, ?), "
            "gpu_type = COALESCE(?, gpu_type), source = COALESCE(?, source), "
            "pod_id = COALESCE(?, pod_id), terminated_at = NULL WHERE id = ?",
            (queue, now, now, gpu_type, source, pod_id, worker_id))
        if cur.rowcount == 0:
            conn.execute(
                "INSERT INTO workers (id, queue, gpu_type, source, pod_id, started_at, "
                "registered_at, last_seen_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (worker_id, queue, gpu_type, source, pod_id, now, now, now))


def touch_worker(worker_id: str) -> None:
    with _db() as conn:
        conn.execute("UPDATE workers SET last_seen_at = ? WHERE id = ?",
                     (time.time(), worker_id))


def set_worker_terminated(worker_id: str) -> None:
    with _db() as conn:
        conn.execute("UPDATE workers SET terminated_at = ? WHERE id = ?",
                     (time.time(), worker_id))


def set_pod_terminated(pod_id: str) -> None:
    """Every worker row the pod carried, booting ones included — a reaped pod's row must stop
    counting whether or not a worker ever registered from it."""
    with _db() as conn:
        conn.execute("UPDATE workers SET terminated_at = ? WHERE pod_id = ? AND terminated_at IS NULL",
                     (time.time(), pod_id))


def live_workers(queue: str, freshness_seconds: float) -> List[Dict]:
    with _db() as conn:
        rows = conn.execute(
            "SELECT * FROM workers WHERE queue = ? AND terminated_at IS NULL "
            "AND registered_at IS NOT NULL AND last_seen_at >= ?",
            (queue, time.time() - freshness_seconds)).fetchall()
    return [dict(r) for r in rows]


def booting_workers(queue: str) -> List[Dict]:
    """Pods created for the queue that no worker has registered from yet."""
    with _db() as conn:
        rows = conn.execute(
            "SELECT * FROM workers WHERE queue = ? AND terminated_at IS NULL "
            "AND registered_at IS NULL ORDER BY started_at", (queue,)).fetchall()
    return [dict(r) for r in rows]


def stale_workers(queue: str, staleness_seconds: float, boot_deadline_seconds: float) -> List[Dict]:
    """Pod-backed workers presumed dead: registered ones that stopped checking in without
    deregistering (crashed or wedged), and booting ones past the boot deadline (image pull
    loop, bad env). Home-box workers (pod_id NULL) are never anyone's to reap."""
    now = time.time()
    with _db() as conn:
        rows = conn.execute(
            "SELECT * FROM workers WHERE queue = ? AND terminated_at IS NULL AND pod_id IS NOT NULL "
            "AND ((registered_at IS NOT NULL AND last_seen_at < ?) "
            "  OR (registered_at IS NULL AND started_at < ?))",
            (queue, now - staleness_seconds, now - boot_deadline_seconds)).fetchall()
    return [dict(r) for r in rows]


def unpriced_pod_workers() -> List[Dict]:
    """Live pod-backed workers whose row does not yet carry the rate RunPod charges for the
    pod — the scaler stamps it from the pod listing, which the worker itself cannot see."""
    with _db() as conn:
        rows = conn.execute(
            "SELECT * FROM workers WHERE terminated_at IS NULL AND pod_id IS NOT NULL "
            "AND usd_per_hour IS NULL").fetchall()
    return [dict(r) for r in rows]


def set_worker_rate(worker_id: str, usd_per_hour: float) -> None:
    with _db() as conn:
        conn.execute("UPDATE workers SET usd_per_hour = ? WHERE id = ?", (usd_per_hour, worker_id))


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


def recent_job_seconds(queue: str, since: float) -> Optional[float]:
    """What one job on this queue costs a worker: the mean exec time of jobs done since `since`
    with the slowest tenth left out, so one stuck job does not make the queue look slow. None
    when nothing finished in the window."""
    with _db() as conn:
        rows = conn.execute(
            "SELECT exec_seconds FROM jobs WHERE queue = ? AND status = 'done' "
            "AND exec_seconds IS NOT NULL AND finished_at >= ? ORDER BY exec_seconds",
            (queue, since)).fetchall()
    if not rows:
        return None
    kept = [r["exec_seconds"] for r in rows][:max(1, len(rows) * 9 // 10)]
    return sum(kept) / len(kept)


def recent_boot_seconds(queue: str, since: float) -> Optional[float]:
    """How long this queue's pods take from create to a registered worker, averaged over the
    pods that registered since `since`. None when none did."""
    with _db() as conn:
        row = conn.execute(
            "SELECT AVG(registered_at - started_at) AS s FROM workers WHERE queue = ? "
            "AND source = 'runpod' AND registered_at IS NOT NULL AND registered_at >= ?",
            (queue, since)).fetchone()
    return row["s"]


def backlog_seconds(queue: str) -> float:
    """Projected GPU-seconds still owed to clear a queue: the reserved estimate of every job not
    yet finished (pending + claimed). What the admin view reads as the live backlog cost."""
    with _db() as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(est_seconds), 0) AS s FROM jobs "
            "WHERE queue = ? AND status IN ('pending', 'claimed')", (queue,)).fetchone()
    return row["s"]


def pending_jobs_head(queue: str, limit: int) -> List[Dict]:
    """The next jobs a worker on this queue will claim, in claim order."""
    with _db() as conn:
        rows = conn.execute(
            "SELECT id, game_id, build_id, est_seconds, created_at FROM jobs "
            "WHERE queue = ? AND status = 'pending' ORDER BY created_at LIMIT ?",
            (queue, limit)).fetchall()
    return [dict(r) for r in rows]


def claimed_jobs(queue: str) -> List[Dict]:
    with _db() as conn:
        rows = conn.execute(
            "SELECT id, game_id, build_id, worker_id, est_seconds, started_at FROM jobs "
            "WHERE queue = ? AND status = 'claimed'", (queue,)).fetchall()
    return [dict(r) for r in rows]


def exec_seconds_by_gpu(since: float) -> Dict[str, float]:
    """GPU-seconds worked per card over jobs finished since `since` — done AND failed, since the
    card ran either way. A job with no recorded card lands under 'unknown'."""
    with _db() as conn:
        rows = conn.execute(
            "SELECT COALESCE(gpu_type, 'unknown') AS gpu, COALESCE(SUM(exec_seconds), 0) AS s "
            "FROM jobs WHERE finished_at >= ? GROUP BY gpu", (since,)).fetchall()
    return {r["gpu"]: r["s"] for r in rows}


def games_exec_seconds_by_gpu(game_ids: List[str]) -> Dict[str, float]:
    """Every finished job the games ever ran, per card — design, builds, art, whenever they
    happened. What a game cost is the whole of it, not the slice inside a window."""
    if not game_ids:
        return {}
    with _db() as conn:
        rows = conn.execute(
            "SELECT COALESCE(gpu_type, 'unknown') AS gpu, COALESCE(SUM(exec_seconds), 0) AS s "
            f"FROM jobs WHERE finished_at IS NOT NULL "
            f"AND game_id IN ({','.join('?' * len(game_ids))}) GROUP BY gpu",
            game_ids).fetchall()
    return {r["gpu"]: r["s"] for r in rows}


def games_built_since(since: float) -> List[str]:
    """Games whose full build finished in the window, whatever its outcome. Fix and change
    rounds are that game's, not a new game — they never count it again."""
    with _db() as conn:
        rows = conn.execute(
            "SELECT DISTINCT game_id FROM builds WHERE kind = 'build' AND finished_at >= ?",
            (since,)).fetchall()
    return [r["game_id"] for r in rows]


def games_change_count(game_ids: List[str]) -> int:
    """Change rounds the games asked for, ever."""
    if not game_ids:
        return 0
    with _db() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM builds WHERE kind = 'change' "
            f"AND game_id IN ({','.join('?' * len(game_ids))})", game_ids).fetchone()
    return row["n"]


def workers_since(since: float) -> List[Dict]:
    """Worker rows alive at any point after `since` — for wall-clock and ghost accounting."""
    with _db() as conn:
        rows = conn.execute(
            "SELECT id, queue, gpu_type, source, pod_id, started_at, last_seen_at, terminated_at "
            "FROM workers WHERE COALESCE(terminated_at, last_seen_at) >= ? OR terminated_at IS NULL",
            (since,)).fetchall()
    return [dict(r) for r in rows]


_DAY = 24 * 3600
_POD_REFUSAL_KEEP_SECONDS = 30 * _DAY


def _bump_pod_day(conn, queue: str, now: float, refusal_kind: Optional[str]) -> None:
    """The never-pruned daily rollup (UTC day) behind the provider-facing ratio: every StartPod
    the scaler executed counts as an attempt, a refusal also counts under its kind."""
    day = time.strftime("%Y-%m-%d", time.gmtime(now))
    conn.execute("INSERT OR IGNORE INTO pod_request_days (queue, day) VALUES (?, ?)", (queue, day))
    col = {None: "", "stock": ", stock_refusals = stock_refusals + 1",
           "other": ", other_refusals = other_refusals + 1"}[refusal_kind]
    conn.execute(f"UPDATE pod_request_days SET attempts = attempts + 1{col} "
                 "WHERE queue = ? AND day = ?", (queue, day))


def record_pod_created(queue: str) -> None:
    with _db() as conn:
        _bump_pod_day(conn, queue, time.time(), None)


def pod_request_totals(queue: str, days: Optional[int] = None) -> Dict:
    """Attempts and refusals by kind over the last `days` UTC days (today included), or all time
    when None; `since` is the earliest day in the window with a row, None when there is none."""
    first = (time.strftime("%Y-%m-%d", time.gmtime(time.time() - (days - 1) * _DAY))
             if days else "")
    with _db() as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(attempts), 0) AS attempts, "
            "COALESCE(SUM(stock_refusals), 0) AS stock_refusals, "
            "COALESCE(SUM(other_refusals), 0) AS other_refusals, MIN(day) AS since "
            "FROM pod_request_days WHERE queue = ? AND day >= ?", (queue, first)).fetchone()
    return dict(row)


def record_pod_refusal(queue: str, kind: str, attempts: List[Dict], error: str) -> None:
    """One StartPod the provider refused on every volume/GPU combination — one row per scaler
    decision, not per combination, because the question the row answers is "how often could we
    not get a pod when we wanted one", and a tick that tries four combos wanted ONE pod. The
    combos and each one's error ride along in `attempts`; `error` is the last provider text.
    `kind` is 'stock' when every combo was refused for lack of stock, else 'other'. Rows older
    than 30 days go on insert, so a refusing provider can never grow the table past a month; the
    daily rollup (`pod_request_days`) keeps the count forever."""
    now = time.time()
    with _db() as conn:
        _bump_pod_day(conn, queue, now, kind)
        conn.execute("DELETE FROM pod_refusals WHERE created_at < ?",
                     (now - _POD_REFUSAL_KEEP_SECONDS,))
        conn.execute(
            "INSERT INTO pod_refusals (queue, kind, attempts, error, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (queue, kind, json.dumps(attempts, ensure_ascii=False), error, now))


def pod_stockout_stats(queue: str, now: float) -> Dict:
    """Stock refusals for a queue: counts over the last hour, day and week, the most recent one,
    and every one in the last hour oldest-first (the admin view walks those to find where the
    current outage began)."""
    with _db() as conn:
        counts = conn.execute(
            "SELECT SUM(CASE WHEN created_at >= ? THEN 1 ELSE 0 END) AS last_1h, "
            "SUM(CASE WHEN created_at >= ? THEN 1 ELSE 0 END) AS last_24h, "
            "COUNT(*) AS last_7d "
            "FROM pod_refusals WHERE queue = ? AND kind = 'stock' AND created_at >= ?",
            (now - 3600, now - _DAY, queue, now - 7 * _DAY)).fetchone()
        last = conn.execute(
            "SELECT created_at, error FROM pod_refusals WHERE queue = ? AND kind = 'stock' "
            "ORDER BY id DESC LIMIT 1", (queue,)).fetchone()
        recent = conn.execute(
            "SELECT created_at, error FROM pod_refusals WHERE queue = ? AND kind = 'stock' "
            "AND created_at >= ? ORDER BY id", (queue, now - 3600)).fetchall()
    return {
        "last_1h": counts["last_1h"] or 0,
        "last_24h": counts["last_24h"] or 0,
        "last_7d": counts["last_7d"],
        "last_at": last["created_at"] if last else None,
        "last_error": last["error"] if last else None,
        "recent": [dict(r) for r in recent],
    }


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
    """Day-bucketed user-action counts: events per (day, kind) and distinct users per day. The
    anonymous landing view counts as a kind but not as a user."""
    with _db() as conn:
        kinds = conn.execute(
            "SELECT date(created_at, 'unixepoch') AS day, kind, COUNT(*) AS n "
            "FROM events WHERE user_id IS NOT NULL AND created_at >= ? "
            "GROUP BY day, kind ORDER BY day",
            (since,)).fetchall()
        users = conn.execute(
            "SELECT date(created_at, 'unixepoch') AS day, COUNT(DISTINCT user_id) AS n "
            "FROM events WHERE user_id IS NOT NULL AND user_id != 'anon' AND created_at >= ? "
            "GROUP BY day ORDER BY day",
            (since,)).fetchall()
    return {"kinds": [dict(r) for r in kinds], "users": [dict(r) for r in users]}
