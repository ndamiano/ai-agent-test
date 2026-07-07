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

_PBKDF2_ROUNDS = 200_000


@dataclass(frozen=True)
class User:
    id: str
    handle: str
    role: str = "user"


def _db_path() -> Path:
    from tools.execution_context import resolve_base_path
    return resolve_base_path() / "auth.db"


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
            created_at    REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS sessions (
            token_hash TEXT PRIMARY KEY,
            user_id    TEXT NOT NULL REFERENCES users(id),
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
            "INSERT INTO users (id, handle, password_hash, role, created_at) VALUES (?, ?, ?, ?, ?)",
            (user.id, user.handle, _hash_password(password), role, time.time()),
        )
    return user


def set_password(handle: str, password: str) -> None:
    if not password:
        raise ValueError("password is required")
    with _db() as conn:
        cur = conn.execute("UPDATE users SET password_hash = ? WHERE handle = ?",
                           (_hash_password(password), handle))
        if cur.rowcount == 0:
            raise ValueError(f"no user {handle!r}")


def get_user(user_id: str) -> Optional[User]:
    with _db() as conn:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return _row_to_user(row) if row else None


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
    with _db() as conn:
        row = conn.execute(
            "SELECT u.* FROM sessions s JOIN users u ON u.id = s.user_id WHERE s.token_hash = ?",
            (_token_hash(token),),
        ).fetchone()
    return _row_to_user(row) if row else None


def revoke_token(token: str) -> None:
    with _db() as conn:
        conn.execute("DELETE FROM sessions WHERE token_hash = ?", (_token_hash(token),))
