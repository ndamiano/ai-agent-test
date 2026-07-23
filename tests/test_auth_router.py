import asyncio
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException
from starlette.requests import Request
from starlette.responses import Response

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from auth import router as auth_router
from auth import store


@pytest.fixture(autouse=True)
def _tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "_db_path", lambda: tmp_path / "auth.db")


def _login(handle, password, scheme="http"):
    """Call the login handler with the Request/Response it now needs (to read the scheme for the
    Secure flag and to set the /play cookie)."""
    request = Request({"type": "http", "scheme": scheme, "headers": [],
                       "method": "POST", "path": "/auth/login", "query_string": b""})
    return asyncio.run(auth_router.login(
        auth_router.LoginRequest(handle=handle, password=password), request, Response()))


def test_login_issues_a_working_token():
    store.create_user("alice", "hunter2")
    result = _login("alice", "hunter2")
    assert result["user"]["handle"] == "alice"
    token = result["token"]
    assert store.resolve_token(token).handle == "alice"


def test_login_rejects_bad_credentials():
    store.create_user("alice", "hunter2")
    with pytest.raises(HTTPException) as exc:
        _login("alice", "wrong")
    assert exc.value.status_code == 401


def test_login_rejects_unknown_user():
    with pytest.raises(HTTPException) as exc:
        _login("ghost", "x")
    assert exc.value.status_code == 401


