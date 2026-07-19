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
    busy_seconds  REAL NOT NULL DEFAULT 0,
    started_at    REAL NOT NULL,
    last_seen_at  REAL,
    terminated_at REAL
);
"""


def _db_path() -> Path:
    from tools.execution_context import resolve_base_path
    return resolve_base_path() / "private" / "platform.db"


@contextmanager
def _db():
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=10000")
    conn.executescript(_SCHEMA)
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
