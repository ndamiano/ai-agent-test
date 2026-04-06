"""Rate limiter for LLM API calls using token bucket algorithm."""

import time
import threading
from typing import Optional


class LLMRateLimiter:
    """
    Thread-safe rate limiter using token bucket algorithm.

    Allows bursts up to capacity, then enforces a steady rate.
    """

    def __init__(self, rate: float = 2.0, capacity: Optional[int] = None):
        """
        Initialize rate limiter.

        Args:
            rate: Maximum requests per second (default: 2)
            capacity: Maximum burst size (default: same as rate)
        """
        self.rate = rate
        self.capacity = capacity if capacity is not None else int(rate)
        self.tokens = float(self.capacity)
        self.last_update = time.time()
        self.lock = threading.Lock()

    def acquire(self, blocking: bool = True, timeout: Optional[float] = None) -> bool:
        """
        Acquire a token for making a request.

        Args:
            blocking: If True, wait until a token is available
            timeout: Maximum time to wait in seconds (only used if blocking=True)

        Returns:
            True if token acquired, False otherwise
        """
        deadline = None if timeout is None else time.time() + timeout

        while True:
            with self.lock:
                now = time.time()
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
                wait_time = min(wait_time, deadline - time.time())
                if wait_time <= 0:
                    return False

            time.sleep(wait_time)

    def reset(self):
        """Reset the rate limiter to full capacity."""
        with self.lock:
            self.tokens = float(self.capacity)
            self.last_update = time.time()


# Global rate limiter instance (2 requests per second)
_llm_rate_limiter = LLMRateLimiter(rate=2.0)


def get_llm_rate_limiter() -> LLMRateLimiter:
    """Get the global LLM rate limiter instance."""
    return _llm_rate_limiter
