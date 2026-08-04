import asyncio

import pytest
from fastapi import HTTPException

from auth import router as auth_router
from auth import store


def _login(handle, password):
    return asyncio.run(auth_router.login(
        auth_router.LoginRequest(handle=handle, password=password)))


def test_login_issues_a_working_token():
    store.create_user("alice", "hunter2-pass1234", email="alice@example.com")
    result = _login("alice", "hunter2-pass1234")
    assert result["user"]["handle"] == "alice"
    token = result["token"]
    assert store.resolve_token(token).handle == "alice"


def test_login_rejects_bad_credentials():
    store.create_user("alice", "hunter2-pass1234", email="alice2@example.com")
    with pytest.raises(HTTPException) as exc:
        _login("alice", "wrong-pass1234")
    assert exc.value.status_code == 401


def test_login_rejects_unknown_user():
    with pytest.raises(HTTPException) as exc:
        _login("ghost", "x-pass1234")
    assert exc.value.status_code == 401


def _change_password(user, current, new):
    return asyncio.run(auth_router.change_password(
        auth_router.PasswordChangeRequest(current_password=current, new_password=new), user))


def test_change_password_requires_the_current_one():
    """A stolen bearer token alone must not be enough to take the account over."""
    from auth.ratelimit import login_throttle
    login_throttle.clear("alice")
    user = store.create_user("alice", "old-pw-pass1234", email="alice3@example.com")

    with pytest.raises(HTTPException) as exc:
        _change_password(user, "wrong-pass1234", "new-pw-pass1234")
    assert exc.value.status_code == 403
    assert store.authenticate("alice", "old-pw-pass1234") is not None

    _change_password(user, "old-pw-pass1234", "new-pw-pass1234")
    assert store.authenticate("alice", "new-pw-pass1234") is not None
    assert store.authenticate("alice", "old-pw-pass1234") is None


def test_change_password_shares_the_login_throttle():
    from auth.ratelimit import login_throttle
    login_throttle.clear("alice")
    user = store.create_user("alice", "old-pw-pass1234", email="alice4@example.com")
    for _ in range(5):
        with pytest.raises(HTTPException):
            _change_password(user, "wrong-pass1234", "new-pw-pass1234")
    with pytest.raises(HTTPException) as exc:
        _change_password(user, "old-pw-pass1234", "new-pw-pass1234")
    assert exc.value.status_code == 429
    login_throttle.clear("alice")


