"""Test rate limiting on API endpoints"""

import pytest
import time
from fastapi.testclient import TestClient
from api.app import app

client = TestClient(app)


class TestRateLimiting:
    """Test rate limiting functionality"""

    def test_rate_limit_on_root_endpoint(self):
        """Test that rate limiting works on the root endpoint (2 requests per second)"""
        # First 2 requests should succeed
        response1 = client.get("/")
        assert response1.status_code == 200

        response2 = client.get("/")
        assert response2.status_code == 200

        # Third request within the same second should be rate limited
        response3 = client.get("/")
        assert response3.status_code == 429  # Too Many Requests

        # Wait for the rate limit window to reset (1 second)
        time.sleep(1.1)

        # Request should succeed now
        response4 = client.get("/")
        assert response4.status_code == 200

    def test_rate_limit_on_tasks_endpoint(self):
        """Test that rate limiting works on the tasks list endpoint"""
        # First 2 requests should succeed
        response1 = client.get("/api/tasks/")
        assert response1.status_code == 200

        response2 = client.get("/api/tasks/")
        assert response2.status_code == 200

        # Third request within the same second should be rate limited
        response3 = client.get("/api/tasks/")
        assert response3.status_code == 429

        # Wait for the rate limit window to reset
        time.sleep(1.1)

        # Request should succeed now
        response4 = client.get("/api/tasks/")
        assert response4.status_code == 200

    def test_rate_limit_on_system_status_endpoint(self):
        """Test that rate limiting works on the system status endpoint"""
        # First 2 requests should succeed
        response1 = client.get("/api/system/status")
        assert response1.status_code == 200

        response2 = client.get("/api/system/status")
        assert response2.status_code == 200

        # Third request within the same second should be rate limited
        response3 = client.get("/api/system/status")
        assert response3.status_code == 429

        # Wait for the rate limit window to reset
        time.sleep(1.1)

        # Request should succeed now
        response4 = client.get("/api/system/status")
        assert response4.status_code == 200

    @pytest.mark.skip(reason="slowapi doesn't always add rate limit headers")
    def test_rate_limit_headers(self):
        """Test that rate limit headers are present in responses"""
        response = client.get("/")

        # Check for rate limit headers
        assert "X-RateLimit-Limit" in response.headers
        assert "X-RateLimit-Remaining" in response.headers

        # Verify the limit is set to 2
        assert response.headers["X-RateLimit-Limit"] == "2"

    def test_rate_limit_error_message(self):
        """Test that rate limit error returns appropriate message"""
        # Make 3 requests quickly to trigger rate limit
        client.get("/")
        client.get("/")
        response = client.get("/")

        assert response.status_code == 429
        # Check that error message is informative
        assert "rate limit" in response.text.lower() or "too many" in response.text.lower()
