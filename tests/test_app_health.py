"""Tests for health and status endpoints after unified connector refactor."""

import unittest
from unittest.mock import patch, MagicMock, AsyncMock
from starlette.testclient import TestClient


class TestHealthEndpoint(unittest.TestCase):
    """Verify the root / health endpoint works with the unified connector."""

    def setUp(self):
        self.mock_connector = MagicMock()
        self.mock_connector.health_check.return_value = True

        # Reset the singleton cache before each test
        from llm_clients.connector_selector import reset_connector_cache
        reset_connector_cache()

    def _get_client(self):
        from api.app import app
        return TestClient(app)

    def test_root_returns_healthy_when_lmstudio_up(self):
        with patch("api.app.get_connector", return_value=self.mock_connector):
            client = self._get_client()
            response = client.get("/")

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "healthy")
        self.assertEqual(data["lmstudio"], "healthy")
        self.assertEqual(data["server"], "running")
        self.assertEqual(data["database"], "healthy")

    def test_root_returns_unhealthy_when_lmstudio_down(self):
        self.mock_connector.health_check.return_value = False

        with patch("api.app.get_connector", return_value=self.mock_connector):
            client = self._get_client()
            response = client.get("/")

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["lmstudio"], "unhealthy")

    def test_root_returns_500_when_health_check_raises(self):
        self.mock_connector.health_check.side_effect = RuntimeError("connection refused")

        with patch("api.app.get_connector", return_value=self.mock_connector):
            client = self._get_client()
            response = client.get("/")

        self.assertEqual(response.status_code, 500)
        self.assertIn("Health check failed", response.json()["detail"])

    def test_health_check_called_exactly_once_per_request(self):
        with patch("api.app.get_connector", return_value=self.mock_connector):
            client = self._get_client()
            client.get("/")

        self.mock_connector.health_check.assert_called_once()


class TestStatusEndpoint(unittest.TestCase):
    """Verify the /api/system/status endpoint works with the unified connector."""

    def setUp(self):
        from llm_clients.connector_selector import reset_connector_cache
        reset_connector_cache()

        self.mock_connector = MagicMock()
        self.mock_connector.health_check_async = AsyncMock(return_value=True)
        self.mock_connector.base_url = "http://localhost:1234"

    def _get_client(self):
        from api.app import app
        return TestClient(app)

    def test_status_returns_connected(self):
        with patch("api.routers.system.get_connector", return_value=self.mock_connector), \
             patch("api.routers.system.task_store") as mock_store, \
             patch("api.routers.system.agent_store") as mock_agents:
            mock_store.list_tasks.return_value = []
            mock_agents.list.return_value = []

            client = self._get_client()
            response = client.get("/api/system/status")

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["lmstudio_connected"])
        self.assertEqual(data["lmstudio_url"], "http://localhost:1234")
        self.assertIn("agent_count", data)
        self.assertIn("task_count", data)

    def test_status_returns_disconnected(self):
        self.mock_connector.health_check_async = AsyncMock(return_value=False)

        with patch("api.routers.system.get_connector", return_value=self.mock_connector), \
             patch("api.routers.system.task_store") as mock_store, \
             patch("api.routers.system.agent_store") as mock_agents:
            mock_store.list_tasks.return_value = []
            mock_agents.list.return_value = []

            client = self._get_client()
            response = client.get("/api/system/status")

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertFalse(data["lmstudio_connected"])


class TestResetConnectorCache(unittest.TestCase):
    """Verify reset_connector_cache creates a new connector on next get_connector call."""

    def setUp(self):
        from llm_clients.connector_selector import reset_connector_cache
        reset_connector_cache()

    def test_reset_creates_new_connector(self):
        from llm_clients.connector_selector import reset_connector_cache, get_connector

        mock_connector_1 = MagicMock()
        mock_connector_2 = MagicMock()

        with patch("llm_clients.connector_selector.get_connector", return_value=mock_connector_1):
            first = get_connector()

        # Reset clears the cache
        reset_connector_cache()

        # Next call should create a new connector
        with patch("llm_clients.connector_selector.get_connector", return_value=mock_connector_2):
            second = get_connector()

        self.assertIsNot(first, second)

    def test_reset_returns_same_connector_when_settings_unchanged(self):
        from llm_clients.connector_selector import get_connector

        mock_connector = MagicMock()

        with patch("llm_clients.connector_selector.get_connector", return_value=mock_connector):
            first = get_connector()
            second = get_connector()

        self.assertIs(first, second)


if __name__ == "__main__":
    unittest.main()