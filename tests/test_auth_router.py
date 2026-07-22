import asyncio
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from auth import router as auth_router
from auth import store


@pytest.fixture(autouse=True)
def _tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "_db_path", lambda: tmp_path / "auth.db")


def test_login_issues_a_working_token():
    store.create_user("alice", "hunter2")
    result = asyncio.run(auth_router.login(auth_router.LoginRequest(handle="alice", password="hunter2")))
    assert result["user"]["handle"] == "alice"
    token = result["token"]
    assert store.resolve_token(token).handle == "alice"


def test_login_rejects_bad_credentials():
    store.create_user("alice", "hunter2")
    with pytest.raises(HTTPException) as exc:
        asyncio.run(auth_router.login(auth_router.LoginRequest(handle="alice", password="wrong")))
    assert exc.value.status_code == 401


def test_login_rejects_unknown_user():
    with pytest.raises(HTTPException) as exc:
        asyncio.run(auth_router.login(auth_router.LoginRequest(handle="ghost", password="x")))
    assert exc.value.status_code == 401


