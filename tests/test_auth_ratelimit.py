import asyncio

import pytest
from fastapi import HTTPException

from auth import router as auth_router
from auth import store
from auth.ratelimit import LoginThrottle, login_throttle


@pytest.fixture(autouse=True)
def _clear_throttle():
    login_throttle.clear("alice")


def _login(handle, password):
    return asyncio.run(auth_router.login(
        auth_router.LoginRequest(handle=handle, password=password)))


def test_throttle_blocks_after_max_failures():
    t = LoginThrottle(window=60, max_failures=3)
    assert t.retry_after("k") == 0
    for _ in range(3):
        t.record_failure("k")
    assert t.retry_after("k") > 0


def test_throttle_success_clears_failures():
    t = LoginThrottle(window=60, max_failures=3)
    for _ in range(3):
        t.record_failure("k")
    assert t.retry_after("k") > 0
    t.clear("k")
    assert t.retry_after("k") == 0


def test_throttle_window_expires(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr("auth.ratelimit.time.time", lambda: now[0])
    t = LoginThrottle(window=60, max_failures=3)
    for _ in range(3):
        t.record_failure("k")
    assert t.retry_after("k") > 0
    now[0] += 61
    assert t.retry_after("k") == 0


def test_throttle_is_per_key():
    t = LoginThrottle(window=60, max_failures=3)
    for _ in range(3):
        t.record_failure("alice")
    assert t.retry_after("alice") > 0
    assert t.retry_after("bob") == 0


def test_login_429s_after_repeated_failures():
    store.create_user("alice", "hunter2")
    for _ in range(5):
        with pytest.raises(HTTPException) as exc:
            _login("alice", "wrong")
        assert exc.value.status_code == 401
    # Sixth attempt is throttled — even a correct password is refused while blocked.
    with pytest.raises(HTTPException) as exc:
        _login("alice", "hunter2")
    assert exc.value.status_code == 429
    assert "Retry-After" in exc.value.headers


def test_successful_login_resets_the_counter():
    store.create_user("alice", "hunter2")
    for _ in range(4):
        with pytest.raises(HTTPException):
            _login("alice", "wrong")
    # A good login before the block threshold clears the streak...
    _login("alice", "hunter2")
    # ...so the next wrong attempt is a 401, not a carried-over 429.
    with pytest.raises(HTTPException) as exc:
        _login("alice", "wrong")
    assert exc.value.status_code == 401
