"""Test LLM API rate limiting"""

import time
from llm_clients.rate_limiter import LLMRateLimiter


class TestLLMRateLimiter:
    """Test the LLM rate limiter functionality"""

    def test_rate_limiter_allows_burst(self):
        """Test that rate limiter allows initial burst"""
        limiter = LLMRateLimiter(rate=2.0, capacity=2)

        # Should allow 2 immediate requests
        assert limiter.acquire(blocking=False) is True
        assert limiter.acquire(blocking=False) is True

        # Third request should fail without blocking
        assert limiter.acquire(blocking=False) is False

    def test_rate_limiter_refills_tokens(self):
        """Test that rate limiter refills tokens over time"""
        limiter = LLMRateLimiter(rate=2.0, capacity=2)

        # Consume all tokens
        limiter.acquire(blocking=False)
        limiter.acquire(blocking=False)

        # Should fail immediately
        assert limiter.acquire(blocking=False) is False

        # Wait for one token to refill (0.5 seconds at 2 req/s)
        time.sleep(0.6)

        # Should succeed now
        assert limiter.acquire(blocking=False) is True

    def test_rate_limiter_blocks_until_available(self):
        """Test that rate limiter blocks until token is available"""
        limiter = LLMRateLimiter(rate=2.0, capacity=1)

        # Consume the token
        limiter.acquire(blocking=False)

        # This should block for ~0.5 seconds
        start_time = time.time()
        assert limiter.acquire(blocking=True, timeout=2) is True
        elapsed = time.time() - start_time

        # Should have waited at least 0.4 seconds (allowing some margin)
        assert elapsed >= 0.4

    def test_rate_limiter_timeout(self):
        """Test that rate limiter respects timeout"""
        limiter = LLMRateLimiter(rate=2.0, capacity=1)

        # Consume the token
        limiter.acquire(blocking=False)

        # Try to acquire with short timeout (should fail)
        start_time = time.time()
        assert limiter.acquire(blocking=True, timeout=0.2) is False
        elapsed = time.time() - start_time

        # Should have timed out quickly
        assert elapsed < 0.3

    def test_rate_limiter_reset(self):
        """Test that reset restores full capacity"""
        limiter = LLMRateLimiter(rate=2.0, capacity=2)

        # Consume all tokens
        limiter.acquire(blocking=False)
        limiter.acquire(blocking=False)

        # Should be empty
        assert limiter.acquire(blocking=False) is False

        # Reset
        limiter.reset()

        # Should be full again
        assert limiter.acquire(blocking=False) is True
        assert limiter.acquire(blocking=False) is True

    def test_rate_limiter_maintains_rate(self):
        """Test that rate limiter maintains the specified rate over time"""
        limiter = LLMRateLimiter(rate=2.0, capacity=2)

        # Make requests and track timing
        requests = 0
        start_time = time.time()

        # Try to make 6 requests (should take ~2 seconds at 2 req/s after initial burst)
        for _ in range(6):
            limiter.acquire(blocking=True, timeout=5)
            requests += 1

        elapsed = time.time() - start_time

        # With a rate of 2 req/s and 6 requests:
        # - First 2 are immediate (burst)
        # - Next 4 take 2 seconds
        # Total should be ~2 seconds
        assert requests == 6
        assert 1.8 <= elapsed <= 2.5  # Allow some margin for timing variations


class TestLLMRateLimiterIntegration:
    """Test rate limiting integration with the connector"""

    def test_connector_rate_limiting(self):
        """Test that the connector properly applies rate limiting"""
        from llm_clients.rate_limiter import get_llm_rate_limiter

        limiter = get_llm_rate_limiter()
        limiter.reset()  # Start fresh

        # Try to acquire 3 tokens rapidly
        assert limiter.acquire(blocking=False) is True
        assert limiter.acquire(blocking=False) is True
        # Third should fail (rate limit)
        assert limiter.acquire(blocking=False) is False

        # Wait and try again
        time.sleep(0.6)
        assert limiter.acquire(blocking=False) is True
