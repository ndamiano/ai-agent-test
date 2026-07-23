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


# Chat turns are authenticated but UNCHARGED (see auth.deps.require_credits): a funded account
# could otherwise loop turns and burn llm-worker GPU forever at zero marginal cost. The cap is
# sized for a human drafting a spec (a heavy session is 10-20 turns/hour), not for loops.
_CHAT_WINDOW_SECONDS = 3600
_CHAT_MAX_TURNS = 30


class RequestThrottle:
    """Sliding-window cap on successful requests per key (unlike LoginThrottle, which counts
    only failures). `hit` records and admits in one step so two racing requests can't both
    slip under the cap."""

    def __init__(self, window: int, max_requests: int):
        self._window = window
        self._max = max_requests
        self._hits: dict[str, deque] = {}
        self._lock = Lock()

    def hit(self, key: str) -> int:
        """Admit and record one request — returns 0, or the seconds to wait if over the cap
        (the refused request is not recorded)."""
        now = time.time()
        cutoff = now - self._window
        with self._lock:
            dq = self._hits.setdefault(key, deque())
            while dq and dq[0] < cutoff:
                dq.popleft()
            if len(dq) >= self._max:
                return max(1, int(dq[0] + self._window - now))
            dq.append(now)
            return 0


chat_throttle = RequestThrottle(_CHAT_WINDOW_SECONDS, _CHAT_MAX_TURNS)
