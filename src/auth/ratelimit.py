"""Login throttle — caps online password guessing against the handful of manual accounts.

Single-process deploy (one uvicorn worker on the one-GPU box), so a process-local sliding
window is enough — no shared store. Keyed on the handle (not the client IP): behind a reverse
proxy / Tailscale Funnel every request shares the proxy's address, so per-IP limiting would
either lock all users out together or need a spoofable X-Forwarded-For. Per-handle directly caps
guesses against a given account, which is the actual threat; a successful login clears it.
"""

import time
from collections import deque
from threading import Lock

_WINDOW_SECONDS = 15 * 60
_MAX_FAILURES = 5


class LoginThrottle:
    def __init__(self, window: int = _WINDOW_SECONDS, max_failures: int = _MAX_FAILURES):
        self._window = window
        self._max = max_failures
        self._failures: dict[str, deque] = {}
        self._lock = Lock()

    def _recent(self, key: str, now: float) -> int:
        dq = self._failures.get(key)
        if not dq:
            return 0
        cutoff = now - self._window
        while dq and dq[0] < cutoff:
            dq.popleft()
        if not dq:
            self._failures.pop(key, None)
            return 0
        return len(dq)

    def retry_after(self, key: str) -> int:
        """Seconds the caller must wait before another attempt, or 0 if not blocked."""
        now = time.time()
        with self._lock:
            if self._recent(key, now) < self._max:
                return 0
            return max(1, int(self._failures[key][0] + self._window - now))

    def record_failure(self, key: str) -> None:
        now = time.time()
        with self._lock:
            self._failures.setdefault(key, deque()).append(now)

    def clear(self, key: str) -> None:
        with self._lock:
            self._failures.pop(key, None)


login_throttle = LoginThrottle()

# Signup has no handle to key on (the account doesn't exist yet), so it throttles by client IP —
# the thing being defended is the invite-code space, and a guesser has one address. Behind a
# proxy that collapses every client to its own address this becomes one shared window; signup is
# a rare act during an invite-gated beta, so that trade is accepted.
signup_throttle = LoginThrottle()
