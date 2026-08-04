"""Persistent user + session store (sqlite).

The first durable identity store on the platform. Passwords are pbkdf2-hashed with a per-user
salt; session tokens are opaque random secrets stored only as a sha256 hash, so a leaked DB
yields neither passwords nor usable tokens. Every operation opens a short-lived connection, so
the store is safe to call from the API threads and the background build threads alike.

Accounts are created through `create_user` (wired to the admin CLI) or `signup` (the invite-code
beta signup): both share `_insert_user`, so the handle/password rules have one source.
"""

import hashlib
import hmac
import re
import secrets
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from config.settings_manager import settings_manager

_PBKDF2_ROUNDS = 600_000

# Length is the whole password rule. Composition requirements (a symbol, a digit, mixed case)
# shrink the search space an attacker must cover and push users toward one predictable shape.
MIN_PASSWORD_LENGTH = 10


# A session token stops resolving this long after it was issued, so a leaked token can't be
# used forever — a re-login mints a fresh one.
SESSION_TTL_SECONDS = 7 * 24 * 3600

# A reset link is a password-equivalent credential that arrives over mail, so it lives briefly.
RESET_TTL_SECONDS = 3600


@dataclass(frozen=True)
class User:
    id: str
    handle: str
    role: str = "user"
    email: str = ""


def _db_path() -> Path:
    # data_dir, never the working_directory: no file-serving route is rooted there, so the auth
    # db can't be reached as if it were a game artifact.
    # Not resolve_base_path(): a tool that repoints the execution_context working dir would fork an
    # empty credential store under it — accounts and the credit ledger silently absent.
    return Path(settings_manager.get_settings()["data_dir"]).resolve() / "auth.db"


# WAL keeps readers and the online-backup snapshot consistent under writers, and switching
# journal_mode needs a lock the busy handler does not cover — so it is applied once per process
# per path, as in db/store. The mode is sticky on the file, so the first connection ever is the
# only one that actually switches.
_WAL_APPLIED: set = set()
_WAL_LOCK = threading.Lock()


@contextmanager
def _db():
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    with _WAL_LOCK:
        if str(path) not in _WAL_APPLIED:
            conn.execute("PRAGMA journal_mode=WAL")
            _WAL_APPLIED.add(str(path))
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            id            TEXT PRIMARY KEY,
            handle        TEXT UNIQUE NOT NULL,
            email         TEXT NOT NULL,
            password_hash TEXT NOT NULL,
            role          TEXT NOT NULL DEFAULT 'user',
            credits       INTEGER NOT NULL DEFAULT 0,
            created_at    REAL NOT NULL
        );
        -- Case-insensitive: nobody remembers which case they signed up with, and two accounts
        -- differing only in case would race for the same reset mail.
        CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email ON users(lower(email));
        CREATE TABLE IF NOT EXISTS sessions (
            token_hash TEXT PRIMARY KEY,
            user_id    TEXT NOT NULL REFERENCES users(id),
            created_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS password_resets (
            token_hash TEXT PRIMARY KEY,
            user_id    TEXT NOT NULL REFERENCES users(id),
            created_at REAL NOT NULL,
            used_at    REAL
        );
        CREATE TABLE IF NOT EXISTS invite_codes (
            code       TEXT PRIMARY KEY,
            created_by TEXT NOT NULL,
            created_at REAL NOT NULL,
            max_uses   INTEGER NOT NULL DEFAULT 1,
            uses       INTEGER NOT NULL DEFAULT 0,
            disabled   INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS credit_transactions (
            id         TEXT PRIMARY KEY,
            user_id    TEXT NOT NULL REFERENCES users(id),
            delta      INTEGER NOT NULL,
            reason     TEXT NOT NULL,
            run_id     TEXT,
            created_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS purchases (
            id             TEXT PRIMARY KEY,
            user_id        TEXT NOT NULL REFERENCES users(id),
            package_id     TEXT NOT NULL,
            credits        INTEGER NOT NULL,
            usd_cents      INTEGER NOT NULL,
            provider_ref   TEXT,
            payment_intent TEXT,
            status         TEXT NOT NULL,
            created_at     REAL NOT NULL,
            completed_at   REAL
        );
        """
    )
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


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
    return User(id=row["id"], handle=row["handle"], role=row["role"], email=row["email"])


class HandleTakenError(ValueError):
    pass


class EmailTakenError(ValueError):
    pass


class InviteCodeError(ValueError):
    pass


# Deliberately permissive: something@something.tld and no spaces. A stricter pattern rejects
# addresses that deliver, and the only proof an address is real is mail arriving at it.
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def clean_email(email: str) -> str:
    email = (email or "").strip()
    if not _EMAIL.match(email):
        raise ValueError("that email address doesn't look right")
    return email


def check_password(password: str) -> None:
    if len(password or "") < MIN_PASSWORD_LENGTH:
        raise ValueError(f"password must be at least {MIN_PASSWORD_LENGTH} characters")


def _insert_user(conn, handle: str, password: str, role: str, email: str) -> User:
    """The one place the handle/password/email rules live — CLI create and invite signup both
    land here."""
    handle = handle.strip()
    if not handle:
        raise ValueError("handle is required")
    check_password(password)
    email = clean_email(email)
    user = User(id=uuid.uuid4().hex[:12], handle=handle, role=role, email=email)
    try:
        conn.execute(
            "INSERT INTO users (id, handle, email, password_hash, role, credits, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (user.id, user.handle, user.email, _hash_password(password), role, 0, time.time()),
        )
    except sqlite3.IntegrityError as e:
        # Two unique constraints reach here; the caller answers them differently.
        raise (EmailTakenError(f"email {email!r} already has an account")
               if "email" in str(e) else HandleTakenError(f"handle {handle!r} already exists"))
    from tools.db_backup import mark_dirty
    mark_dirty()
    return user


def create_user(handle: str, password: str, role: str = "user", email: str = "") -> User:
    with _db() as conn:
        return _insert_user(conn, handle, password, role, email)


def set_password(handle: str, password: str) -> None:
    """Set a password and END EVERY SESSION the account has. Someone changing their password
    after a scare is trying to evict whoever else is in — leaving other tokens live for the rest
    of their week-long TTL is the opposite of what they asked for. The caller re-issues for the
    session doing the change."""
    check_password(password)
    with _db() as conn:
        cur = conn.execute("UPDATE users SET password_hash = ? WHERE handle = ?",
                           (_hash_password(password), handle))
        if cur.rowcount == 0:
            raise ValueError(f"no user {handle!r}")
        conn.execute("DELETE FROM sessions WHERE user_id = "
                     "(SELECT id FROM users WHERE handle = ?)", (handle,))
    from tools.db_backup import mark_dirty
    mark_dirty()


def set_email(user_id: str, email: str) -> str:
    email = clean_email(email)
    with _db() as conn:
        try:
            cur = conn.execute("UPDATE users SET email = ? WHERE id = ?", (email, user_id))
        except sqlite3.IntegrityError:
            raise EmailTakenError(f"email {email!r} already has an account")
        if cur.rowcount == 0:
            raise ValueError(f"no user {user_id!r}")
    from tools.db_backup import mark_dirty
    mark_dirty()
    return email


def delete_user(user_id: str) -> None:
    """Remove an account and everything keyed to it in this store. Games live in the platform
    db and are the caller's to deal with."""
    with _db() as conn:
        for table in ("sessions", "password_resets", "credit_transactions", "purchases"):
            conn.execute(f"DELETE FROM {table} WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
    from tools.db_backup import mark_dirty
    mark_dirty()


def get_user_by_handle(handle: str) -> Optional[User]:
    with _db() as conn:
        row = conn.execute("SELECT * FROM users WHERE handle = ?", (handle,)).fetchone()
    return _row_to_user(row) if row else None


def get_user_by_email(email: str) -> Optional[User]:
    with _db() as conn:
        row = conn.execute("SELECT * FROM users WHERE lower(email) = lower(?)",
                           ((email or "").strip(),)).fetchone()
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


# ── Password resets: a mailed, single-use, short-lived credential ────────────────────────────


def issue_reset_token(user_id: str) -> str:
    """A fresh reset token, and every earlier one for this account dies — a user who clicks
    "forgot" twice must not leave a spare key live in their inbox."""
    token = secrets.token_urlsafe(32)
    with _db() as conn:
        conn.execute("DELETE FROM password_resets WHERE user_id = ? AND used_at IS NULL",
                     (user_id,))
        conn.execute("INSERT INTO password_resets (token_hash, user_id, created_at) "
                     "VALUES (?, ?, ?)", (_token_hash(token), user_id, time.time()))
    return token


def consume_reset_token(token: str) -> Optional[User]:
    """Spend a reset token, once. The claim is an UPDATE guarded on still-unused, so two
    requests carrying the same token can never both come back with a user."""
    if not token:
        return None
    cutoff = time.time() - RESET_TTL_SECONDS
    with _db() as conn:
        cur = conn.execute(
            "UPDATE password_resets SET used_at = ? "
            "WHERE token_hash = ? AND used_at IS NULL AND created_at > ?",
            (time.time(), _token_hash(token), cutoff))
        if cur.rowcount == 0:
            return None
        row = conn.execute(
            "SELECT u.* FROM password_resets r JOIN users u ON u.id = r.user_id "
            "WHERE r.token_hash = ?", (_token_hash(token),)).fetchone()
    return _row_to_user(row) if row else None


# ── Invite codes: what gates beta signup — unguessable, admin-minted, use-counted ────────────

# No 0/o/1/l/i — codes get read aloud and retyped. 8 chars over 31 symbols ≈ 40 bits, which with
# the per-IP signup throttle is out of guessing range.
_CODE_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"


def _generate_code() -> str:
    def quad() -> str:
        return "".join(secrets.choice(_CODE_ALPHABET) for _ in range(4))
    return f"gs-{quad()}-{quad()}"


def create_invite(created_by: str, max_uses: int = 1) -> str:
    if max_uses < 1:
        raise ValueError("max_uses must be at least 1")
    code = _generate_code()
    with _db() as conn:
        conn.execute(
            "INSERT INTO invite_codes (code, created_by, created_at, max_uses, uses, disabled) "
            "VALUES (?, ?, ?, ?, 0, 0)",
            (code, created_by, time.time(), max_uses),
        )
    return code


def list_invites() -> List[dict]:
    with _db() as conn:
        rows = conn.execute("SELECT * FROM invite_codes ORDER BY created_at DESC").fetchall()
    return [dict(r) for r in rows]


def disable_invite(code: str) -> None:
    with _db() as conn:
        cur = conn.execute("UPDATE invite_codes SET disabled = 1 WHERE code = ?", (code,))
        if cur.rowcount == 0:
            raise InviteCodeError(f"no invite code {code!r}")


def signup(handle: str, password: str, code: str, email: str = "") -> User:
    """Redeem an invite code and create the account, atomically: one transaction holds both the
    guarded use-increment and the user insert, so a failed signup (taken handle) rolls the burn
    back and two racers on a code's last use can't both get through — the `uses < max_uses`
    guard admits exactly one."""
    code = code.strip().lower()
    with _db() as conn:
        row = conn.execute("SELECT * FROM invite_codes WHERE code = ?", (code,)).fetchone()
        if row is None:
            raise InviteCodeError("invalid invite code")
        if row["disabled"]:
            raise InviteCodeError("this invite code has been disabled")
        cur = conn.execute(
            "UPDATE invite_codes SET uses = uses + 1 WHERE code = ? AND disabled = 0 "
            "AND uses < max_uses",
            (code,),
        )
        if cur.rowcount == 0:
            raise InviteCodeError("this invite code has no uses left")
        return _insert_user(conn, handle, password, "user", email)


# Balance lives on the user row; every change also lands a signed row in credit_transactions, so
# the balance always reconciles with the log's sum.
def _log_txn(conn, user_id: str, delta: int, reason: str, run_id: Optional[str]) -> None:
    conn.execute(
        "INSERT INTO credit_transactions (id, user_id, delta, reason, run_id, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (uuid.uuid4().hex[:16], user_id, delta, reason, run_id, time.time()),
    )
    # Every credit movement lands here — money rows snapshot ahead of the backup interval.
    from tools.db_backup import mark_dirty
    mark_dirty()


def balance(user_id: str) -> int:
    with _db() as conn:
        row = conn.execute("SELECT credits FROM users WHERE id = ?", (user_id,)).fetchone()
    return row["credits"] if row else 0


def _apply_grant(conn, user_id: str, n: int, reason: str, run_id: Optional[str]) -> int:
    conn.execute("UPDATE users SET credits = credits + ? WHERE id = ?", (n, user_id))
    _log_txn(conn, user_id, n, reason, run_id)
    row = conn.execute("SELECT credits FROM users WHERE id = ?", (user_id,)).fetchone()
    return row["credits"] if row else 0


def grant(user_id: str, n: int, reason: str = "grant", run_id: Optional[str] = None) -> int:
    with _db() as conn:
        return _apply_grant(conn, user_id, n, reason, run_id)


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


# A purchase is the storefront's record of one checkout: what was bought, for how much, and
# whether the provider confirmed it. The credits themselves still move only through the ledger —
# `complete_purchase` flips the row and grants in ONE transaction, so the started→completed flip
# is the exactly-once gate.
@dataclass(frozen=True)
class Purchase:
    id: str
    user_id: str
    package_id: str
    credits: int
    usd_cents: int
    provider_ref: Optional[str]
    payment_intent: Optional[str]
    status: str
    created_at: float
    completed_at: Optional[float]


def _row_to_purchase(row: sqlite3.Row) -> Purchase:
    return Purchase(id=row["id"], user_id=row["user_id"], package_id=row["package_id"],
                    credits=row["credits"], usd_cents=row["usd_cents"],
                    provider_ref=row["provider_ref"], payment_intent=row["payment_intent"],
                    status=row["status"],
                    created_at=row["created_at"], completed_at=row["completed_at"])


def create_purchase(user_id: str, package_id: str, credits: int, usd_cents: int) -> Purchase:
    purchase = Purchase(id=uuid.uuid4().hex[:16], user_id=user_id, package_id=package_id,
                        credits=credits, usd_cents=usd_cents, provider_ref=None,
                        payment_intent=None, status="started", created_at=time.time(),
                        completed_at=None)
    with _db() as conn:
        conn.execute(
            "INSERT INTO purchases (id, user_id, package_id, credits, usd_cents, status, "
            "created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (purchase.id, purchase.user_id, purchase.package_id, purchase.credits,
             purchase.usd_cents, purchase.status, purchase.created_at),
        )
    return purchase


def set_purchase_ref(purchase_id: str, provider_ref: str) -> None:
    with _db() as conn:
        conn.execute("UPDATE purchases SET provider_ref = ? WHERE id = ?",
                     (provider_ref, purchase_id))


def set_payment_intent(purchase_id: str, payment_intent: str) -> None:
    """The provider's payment id, recorded at completion — what a later refund or chargeback
    event names, since those events never carry our purchase id."""
    with _db() as conn:
        conn.execute("UPDATE purchases SET payment_intent = ? WHERE id = ?",
                     (payment_intent, purchase_id))


def purchase_by_payment_intent(payment_intent: str) -> Optional[Purchase]:
    with _db() as conn:
        row = conn.execute("SELECT * FROM purchases WHERE payment_intent = ?",
                           (payment_intent,)).fetchone()
    return _row_to_purchase(row) if row else None


def revoke_credits(user_id: str, n: int, reason: str, run_id: Optional[str] = None) -> int:
    """Subtract credits with NO floor — a refunded purchase takes its credits back even if they
    were already spent, and a negative balance is what blocks further builds. Returns the new
    balance."""
    with _db() as conn:
        conn.execute("UPDATE users SET credits = credits - ? WHERE id = ?", (n, user_id))
        _log_txn(conn, user_id, -n, reason, run_id)
        row = conn.execute("SELECT credits FROM users WHERE id = ?", (user_id,)).fetchone()
        return row["credits"] if row else 0


def refund_purchase(purchase_id: str) -> Optional[int]:
    """Flip completed→refunded and take the credits back, atomically — the mirror of
    complete_purchase, idempotent the same way. Returns the new balance if THIS call did the
    revoking, None if the purchase was not in a refundable state."""
    with _db() as conn:
        cur = conn.execute(
            "UPDATE purchases SET status = 'refunded' WHERE id = ? AND status = 'completed'",
            (purchase_id,),
        )
        if cur.rowcount != 1:
            return None
        row = conn.execute("SELECT user_id, credits FROM purchases WHERE id = ?",
                           (purchase_id,)).fetchone()
        conn.execute("UPDATE users SET credits = credits - ? WHERE id = ?",
                     (row["credits"], row["user_id"]))
        _log_txn(conn, row["user_id"], -row["credits"], "purchase_refund", None)
        bal = conn.execute("SELECT credits FROM users WHERE id = ?",
                           (row["user_id"],)).fetchone()
        return bal["credits"] if bal else 0


def get_purchase(purchase_id: str) -> Optional[Purchase]:
    with _db() as conn:
        row = conn.execute("SELECT * FROM purchases WHERE id = ?", (purchase_id,)).fetchone()
    return _row_to_purchase(row) if row else None


def complete_purchase(purchase_id: str) -> Optional[int]:
    """Flip started→completed and grant the purchase's credits, atomically. Returns the new
    balance if THIS call did the granting, None if the purchase was already completed (or is
    unknown) — the caller that gets None knows nothing was credited by it."""
    with _db() as conn:
        cur = conn.execute(
            "UPDATE purchases SET status = 'completed', completed_at = ? "
            "WHERE id = ? AND status = 'started'",
            (time.time(), purchase_id),
        )
        if cur.rowcount != 1:
            return None
        row = conn.execute("SELECT user_id, credits FROM purchases WHERE id = ?",
                           (purchase_id,)).fetchone()
        return _apply_grant(conn, row["user_id"], row["credits"], "purchase", None)


def list_purchases(user_id: str) -> List[Purchase]:
    with _db() as conn:
        rows = conn.execute("SELECT * FROM purchases WHERE user_id = ? ORDER BY created_at DESC",
                            (user_id,)).fetchall()
    return [_row_to_purchase(r) for r in rows]
