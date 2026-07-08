import time
import threading
from typing import Callable, Optional


class LLMRateLimiter:
    """Thread-safe token bucket rate limiter."""

    def __init__(
        self,
        rate: float = 2.0,
        capacity: Optional[int] = None,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.rate = rate
        self.capacity = capacity if capacity is not None else int(rate)
        self.tokens = float(self.capacity)
        self._clock = clock
        self._sleep = sleep
        self.last_update = clock()
        self.lock = threading.Lock()

    def acquire(self, blocking: bool = True, timeout: Optional[float] = None) -> bool:
        deadline = None if timeout is None else self._clock() + timeout

        while True:
            with self.lock:
                now = self._clock()
                elapsed = now - self.last_update

                # Add tokens based on elapsed time
                self.tokens = min(self.capacity, self.tokens + elapsed * self.rate)
                self.last_update = now

                # Try to consume a token
                if self.tokens >= 1:
                    self.tokens -= 1
                    return True

                # If not blocking, return immediately
                if not blocking:
                    return False

                # Check timeout
                if deadline is not None and now >= deadline:
                    return False

                # Calculate wait time
                wait_time = (1.0 - self.tokens) / self.rate

            # Wait outside the lock
            if deadline is not None:
                wait_time = min(wait_time, deadline - self._clock())
                if wait_time <= 0:
                    return False

            self._sleep(wait_time)

    def reset(self):
        with self.lock:
            self.tokens = float(self.capacity)
            self.last_update = self._clock()


# Capacity covers the largest parallel-fix batch (settings parallel_fixes <= 8), so a whole batch
# starts unthrottled; the rate only paces sustained bursts.
_llm_rate_limiter = LLMRateLimiter(rate=2.0, capacity=8)


def get_llm_rate_limiter() -> LLMRateLimiter:
    return _llm_rate_limiter
