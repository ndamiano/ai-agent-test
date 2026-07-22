import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import sqlite3

import auth.store as s
from auth import store


@pytest.fixture(autouse=True)
def _tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "_db_path", lambda: tmp_path / "auth.db")


def test_password_is_hashed_not_stored_plaintext(tmp_path):
    user = store.create_user("alice", "hunter2")
    conn = sqlite3.connect(str(tmp_path / "auth.db"))
    stored = conn.execute("SELECT password_hash FROM users WHERE id = ?", (user.id,)).fetchone()[0]
    conn.close()
    assert "hunter2" not in stored
    assert store._verify_password("hunter2", stored)
    assert not store._verify_password("wrong", stored)


def test_create_user_rejects_duplicate_handle():
    store.create_user("alice", "pw")
    with pytest.raises(ValueError):
        store.create_user("alice", "other")


def test_authenticate_success_and_failure():
    store.create_user("alice", "hunter2")
    ok = store.authenticate("alice", "hunter2")
    assert ok is not None and ok.handle == "alice"
    assert store.authenticate("alice", "nope") is None
    assert store.authenticate("ghost", "hunter2") is None


def test_role_is_persisted():
    store.create_user("root", "pw", role="admin")
    assert store.get_user_by_handle("root").role == "admin"


def test_set_password_changes_credential_and_404s_unknown():
    store.create_user("alice", "old")
    store.set_password("alice", "new")
    assert store.authenticate("alice", "old") is None
    assert store.authenticate("alice", "new") is not None
    with pytest.raises(ValueError):
        store.set_password("ghost", "x")


def test_token_issue_resolve_and_revoke():
    user = store.create_user("alice", "pw")
    token = store.issue_token(user.id)
    resolved = store.resolve_token(token)
    assert resolved is not None and resolved.id == user.id

    assert store.resolve_token(None) is None
    assert store.resolve_token("not-a-real-token") is None

    store.revoke_token(token)
    assert store.resolve_token(token) is None


def test_token_stops_resolving_after_its_ttl(monkeypatch):
    user = store.create_user("alice", "pw")
    token = store.issue_token(user.id)
    assert store.resolve_token(token) is not None

    # Age the session past the TTL by shifting "now" forward — the token no longer resolves.
    real_time = s.time.time
    monkeypatch.setattr(s.time, "time",
                        lambda: real_time() + store.SESSION_TTL_SECONDS + 1)
    assert store.resolve_token(token) is None


def test_token_stored_only_as_hash(tmp_path):
    user = store.create_user("alice", "pw")
    token = store.issue_token(user.id)
    conn = sqlite3.connect(str(tmp_path / "auth.db"))
    rows = [r[0] for r in conn.execute("SELECT token_hash FROM sessions").fetchall()]
    conn.close()
    assert token not in rows  # the raw token never touches disk
