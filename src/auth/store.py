"""Persistent user + session store (sqlite).

The first durable identity store on the platform. Passwords are pbkdf2-hashed with a per-user
salt; session tokens are opaque random secrets stored only as a sha256 hash, so a leaked DB
yields neither passwords nor usable tokens. Every operation opens a short-lived connection, so
the store is safe to call from the API threads and the background build threads alike.

Accounts are created through `create_user` (wired to the admin CLI) — there is no signup path.
"""

import hashlib
import hmac
import secrets
import sqlite3
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from config.settings_manager import settings_manager

_PBKDF2_ROUNDS = 200_000

# Credits seeded on account creation (grant-on-create, deduct-per-build).
INITIAL_CREDITS = 100

# A session token stops resolving this long after it was issued, so a leaked token can't be
# used forever — a re-login mints a fresh one.
SESSION_TTL_SECONDS = 30 * 24 * 3600


@dataclass(frozen=True)
class User:
    id: str
    handle: str
    role: str = "user"


def _db_path() -> Path:
    # data_dir, never the working_directory: no file-serving route is rooted there, so the auth
    # db can't be reached as if it were a game artifact.
    # Not resolve_base_path(): a tool that repoints the execution_context working dir would fork an
    # empty credential store under it — accounts and the credit ledger silently absent.
    return Path(settings_manager.get_settings()["data_dir"]).resolve() / "auth.db"


@contextmanager
def _db():
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            id            TEXT PRIMARY KEY,
            handle        TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            role          TEXT NOT NULL DEFAULT 'user',
            credits       INTEGER NOT NULL DEFAULT 0,
            created_at    REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS sessions (
            token_hash TEXT PRIMARY KEY,
            user_id    TEXT NOT NULL REFERENCES users(id),
            created_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS credit_transactions (
            id         TEXT PRIMARY KEY,
            user_id    TEXT NOT NULL REFERENCES users(id),
            delta      INTEGER NOT NULL,
            reason     TEXT NOT NULL,
            run_id     TEXT,
            created_at REAL NOT NULL
        );
        """
    )
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


# ── password + token hashing ──────────────────────────────────────────────────
def _hash_password(password: str, salt: Optional[bytes] = None) -> str:
    salt = salt or secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _PBKDF2_ROUNDS)
    return f"{salt.hex()}${dk.hex()}"


def _verify_password(password: str, stored: str) -> bool:
    try:
        salt_hex, expected = stored.split("$", 1)
    except ValueError:
        return False
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                             bytes.fromhex(salt_hex), _PBKDF2_ROUNDS)
    return hmac.compare_digest(dk.hex(), expected)


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _row_to_user(row: sqlite3.Row) -> User:
    return User(id=row["id"], handle=row["handle"], role=row["role"])


# ── users ─────────────────────────────────────────────────────────────────────
def create_user(handle: str, password: str, role: str = "user") -> User:
    handle = handle.strip()
    if not handle:
        raise ValueError("handle is required")
    if not password:
        raise ValueError("password is required")
    user = User(id=uuid.uuid4().hex[:12], handle=handle, role=role)
    with _db() as conn:
        if conn.execute("SELECT 1 FROM users WHERE handle = ?", (handle,)).fetchone():
            raise ValueError(f"handle {handle!r} already exists")
        conn.execute(
            "INSERT INTO users (id, handle, password_hash, role, credits, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (user.id, user.handle, _hash_password(password), role, INITIAL_CREDITS, time.time()),
        )
        _log_txn(conn, user.id, INITIAL_CREDITS, "initial_grant", None)
    return user


def set_password(handle: str, password: str) -> None:
    if not password:
        raise ValueError("password is required")
    with _db() as conn:
        cur = conn.execute("UPDATE users SET password_hash = ? WHERE handle = ?",
                           (_hash_password(password), handle))
        if cur.rowcount == 0:
            raise ValueError(f"no user {handle!r}")


def get_user_by_handle(handle: str) -> Optional[User]:
    with _db() as conn:
        row = conn.execute("SELECT * FROM users WHERE handle = ?", (handle,)).fetchone()
    return _row_to_user(row) if row else None


def list_users() -> List[User]:
    with _db() as conn:
        rows = conn.execute("SELECT * FROM users ORDER BY created_at").fetchall()
    return [_row_to_user(r) for r in rows]


def authenticate(handle: str, password: str) -> Optional[User]:
    with _db() as conn:
        row = conn.execute("SELECT * FROM users WHERE handle = ?", (handle,)).fetchone()
    if row is None or not _verify_password(password, row["password_hash"]):
        return None
    return _row_to_user(row)


# ── sessions (bearer tokens) ───────────────────────────────────────────────────
def issue_token(user_id: str) -> str:
    token = secrets.token_urlsafe(32)
    with _db() as conn:
        conn.execute("INSERT INTO sessions (token_hash, user_id, created_at) VALUES (?, ?, ?)",
                     (_token_hash(token), user_id, time.time()))
    return token


def resolve_token(token: Optional[str]) -> Optional[User]:
    if not token:
        return None
    cutoff = time.time() - SESSION_TTL_SECONDS
    with _db() as conn:
        row = conn.execute(
            "SELECT u.* FROM sessions s JOIN users u ON u.id = s.user_id "
            "WHERE s.token_hash = ? AND s.created_at > ?",
            (_token_hash(token), cutoff),
        ).fetchone()
    return _row_to_user(row) if row else None


def revoke_token(token: str) -> None:
    with _db() as conn:
        conn.execute("DELETE FROM sessions WHERE token_hash = ?", (_token_hash(token),))


# ── credit ledger ───────────────────────────────────────────────────────────────
# Balance lives on the user row; every change also lands a signed row in
# credit_transactions, so the balance always reconciles with the log's sum. Deduct is a
# single check-and-decrement statement — atomic under concurrency, refuses to go negative,
# and returns False (not an exception) so the caller branches on it rather than catching.
def _log_txn(conn, user_id: str, delta: int, reason: str, run_id: Optional[str]) -> None:
    conn.execute(
        "INSERT INTO credit_transactions (id, user_id, delta, reason, run_id, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (uuid.uuid4().hex[:16], user_id, delta, reason, run_id, time.time()),
    )


def balance(user_id: str) -> int:
    with _db() as conn:
        row = conn.execute("SELECT credits FROM users WHERE id = ?", (user_id,)).fetchone()
    return row["credits"] if row else 0


def grant(user_id: str, n: int, reason: str = "grant", run_id: Optional[str] = None) -> int:
    """Add `n` credits and log it. Returns the new balance."""
    with _db() as conn:
        conn.execute("UPDATE users SET credits = credits + ? WHERE id = ?", (n, user_id))
        _log_txn(conn, user_id, n, reason, run_id)
        row = conn.execute("SELECT credits FROM users WHERE id = ?", (user_id,)).fetchone()
    return row["credits"] if row else 0


def deduct(user_id: str, n: int, reason: str, run_id: Optional[str] = None) -> bool:
    """Atomically remove `n` credits, refusing to go negative. Returns True if charged, False if the
    balance was insufficient (or the user is unknown) — no partial deduction, no exception."""
    with _db() as conn:
        cur = conn.execute(
            "UPDATE users SET credits = credits - ? WHERE id = ? AND credits >= ?",
            (n, user_id, n),
        )
        if cur.rowcount != 1:
            return False
        _log_txn(conn, user_id, -n, reason, run_id)
    return True


def refund(user_id: str, n: int, reason: str, run_id: Optional[str] = None) -> int:
    """Return `n` credits for a build that never ran. Returns the new balance."""
    return grant(user_id, n, reason, run_id)
