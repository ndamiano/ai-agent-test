"""Test LLM API rate limiting"""

from llm_clients.rate_limiter import LLMRateLimiter, get_llm_rate_limiter


class FakeClock:
    """Controllable monotonic clock: sleep advances virtual time instead of
    blocking, so token-bucket timing is exercised deterministically with zero
    real wall-clock waiting (the old wall-clock asserts flaked on loaded CI)."""

    def __init__(self):
        self.now = 0.0

    def time(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += max(0.0, seconds)

    def advance(self, seconds: float) -> None:
        self.now += seconds


class TestLLMRateLimiter:
    """Test the LLM rate limiter functionality"""

    def test_rate_limiter_allows_burst(self):
        # Contract: a fresh bucket permits an immediate burst up to capacity, then refuses.
        clock = FakeClock()
        limiter = LLMRateLimiter(rate=2.0, capacity=2, clock=clock.time, sleep=clock.sleep)

        assert limiter.acquire(blocking=False) is True
        assert limiter.acquire(blocking=False) is True

        # Capacity exhausted, no time has passed: third non-blocking acquire fails.
        assert limiter.acquire(blocking=False) is False

    def test_rate_limiter_refills_tokens(self):
        # Contract: tokens refill at the configured rate (2/s => one token per 0.5s).
        clock = FakeClock()
        limiter = LLMRateLimiter(rate=2.0, capacity=2, clock=clock.time, sleep=clock.sleep)

        limiter.acquire(blocking=False)
        limiter.acquire(blocking=False)
        assert limiter.acquire(blocking=False) is False

        # Not enough elapsed for a full token yet — still refused.
        clock.advance(0.4)
        assert limiter.acquire(blocking=False) is False

        # Past the 0.5s refill threshold — one token is back.
        clock.advance(0.2)
        assert limiter.acquire(blocking=False) is True

    def test_rate_limiter_blocks_until_available(self):
        # Contract: a blocking acquire waits exactly until a token refills, then succeeds.
        clock = FakeClock()
        limiter = LLMRateLimiter(rate=2.0, capacity=1, clock=clock.time, sleep=clock.sleep)

        limiter.acquire(blocking=False)

        assert limiter.acquire(blocking=True, timeout=2) is True
        # It slept forward to the ~0.5s refill point rather than spinning or over-waiting.
        assert clock.now >= 0.4
        assert clock.now <= 0.6

    def test_rate_limiter_timeout(self):
        # Contract: a blocking acquire gives up once the timeout window elapses.
        clock = FakeClock()
        limiter = LLMRateLimiter(rate=2.0, capacity=1, clock=clock.time, sleep=clock.sleep)

        limiter.acquire(blocking=False)

        # Timeout (0.2s) is shorter than the 0.5s refill, so it must fail near the deadline.
        assert limiter.acquire(blocking=True, timeout=0.2) is False
        assert clock.now <= 0.3

    def test_rate_limiter_reset(self):
        # Contract: reset restores full capacity regardless of prior drain.
        clock = FakeClock()
        limiter = LLMRateLimiter(rate=2.0, capacity=2, clock=clock.time, sleep=clock.sleep)

        limiter.acquire(blocking=False)
        limiter.acquire(blocking=False)
        assert limiter.acquire(blocking=False) is False

        limiter.reset()

        assert limiter.acquire(blocking=False) is True
        assert limiter.acquire(blocking=False) is True


class TestLLMRateLimiterIntegration:
    """Test rate limiting integration with the connector"""

    def test_connector_rate_limiting(self, monkeypatch):
        # Contract: the shared singleton is sized so a full parallel-fix batch (8) bursts
        # through, then the rate gates until tokens refill. Driven on a fake clock so the
        # singleton's real capacity/rate wiring is verified without wall-clock waits.
        clock = FakeClock()
        limiter = get_llm_rate_limiter()
        monkeypatch.setattr(limiter, "_clock", clock.time)
        monkeypatch.setattr(limiter, "_sleep", clock.sleep)
        limiter.reset()

        for _ in range(8):
            assert limiter.acquire(blocking=False) is True
        assert limiter.acquire(blocking=False) is False

        # After a full refill window a token is available again.
        clock.advance(0.6)
        assert limiter.acquire(blocking=False) is True
