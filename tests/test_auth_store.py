
import pytest

import sqlite3

import auth.store as s
from auth import store


def test_password_is_hashed_not_stored_plaintext(tmp_path):
    user = store.create_user("alice", "hunter2-pass1234", email="alice@example.com")
    conn = sqlite3.connect(str(tmp_path / "auth.db"))
    stored = conn.execute("SELECT password_hash FROM users WHERE id = ?", (user.id,)).fetchone()[0]
    conn.close()
    assert "hunter2" not in stored
    assert store._verify_password("hunter2-pass1234", stored)
    assert not store._verify_password("wrong", stored)


def test_db_runs_in_wal_mode(tmp_path):
    # The online-backup snapshot reads the WAL, so the store must open the db into it.
    store.create_user("alice", "pw-pass1234", email="alice2@example.com")
    conn = sqlite3.connect(str(tmp_path / "auth.db"))
    mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    conn.close()
    assert mode == "wal"


def test_create_user_rejects_duplicate_handle():
    store.create_user("alice", "pw-pass1234", email="alice3@example.com")
    with pytest.raises(ValueError):
        store.create_user("alice", "other-pass1234", email="alice4@example.com")


def test_authenticate_success_and_failure():
    store.create_user("alice", "hunter2-pass1234", email="alice5@example.com")
    ok = store.authenticate("alice", "hunter2-pass1234")
    assert ok is not None and ok.handle == "alice"
    assert store.authenticate("alice", "nope-pass1234") is None
    assert store.authenticate("ghost", "hunter2-pass1234") is None


def test_role_is_persisted():
    store.create_user("root", "pw-pass1234", role="admin", email="root@example.com")
    assert store.get_user_by_handle("root").role == "admin"


def test_set_password_changes_credential_and_404s_unknown():
    store.create_user("alice", "old-pass1234", email="alice6@example.com")
    store.set_password("alice", "new-pass1234")
    assert store.authenticate("alice", "old-pass1234") is None
    assert store.authenticate("alice", "new-pass1234") is not None
    with pytest.raises(ValueError):
        store.set_password("ghost", "x-pass1234")


def test_token_issue_resolve_and_revoke():
    user = store.create_user("alice", "pw-pass1234", email="alice7@example.com")
    token = store.issue_token(user.id)
    resolved = store.resolve_token(token)
    assert resolved is not None and resolved.id == user.id

    assert store.resolve_token(None) is None
    assert store.resolve_token("not-a-real-token") is None

    store.revoke_token(token)
    assert store.resolve_token(token) is None


def test_token_stops_resolving_after_its_ttl(monkeypatch):
    user = store.create_user("alice", "pw-pass1234", email="alice8@example.com")
    token = store.issue_token(user.id)
    assert store.resolve_token(token) is not None

    real_time = s.time.time
    monkeypatch.setattr(s.time, "time",
                        lambda: real_time() + store.SESSION_TTL_SECONDS + 1)
    assert store.resolve_token(token) is None


def test_token_stored_only_as_hash(tmp_path):
    user = store.create_user("alice", "pw-pass1234", email="alice9@example.com")
    token = store.issue_token(user.id)
    conn = sqlite3.connect(str(tmp_path / "auth.db"))
    rows = [r[0] for r in conn.execute("SELECT token_hash FROM sessions").fetchall()]
    conn.close()
    assert token not in rows


def test_password_hashing_stays_expensive_in_production():
    """The suite runs on 1_000 rounds (conftest `cheap_password_hashing`); the shipped number is
    what makes a stolen hash costly, and nothing else asserts it."""
    import ast

    with open(s.__file__) as fh:
        src = ast.parse(fh.read())
    rounds = next(n.value.value for n in ast.walk(src)
                  if isinstance(n, ast.Assign) and n.targets[0].id == "_PBKDF2_ROUNDS")
    assert rounds >= 600_000
