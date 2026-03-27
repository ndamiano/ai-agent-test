"""Tests for health and status endpoints after lazy connector refactor."""

import unittest
from unittest.mock import patch, MagicMock, AsyncMock
from starlette.testclient import TestClient


class TestHealthEndpoint(unittest.TestCase):
    """Verify the root / health endpoint works with the lazy-initialized connector."""

    def setUp(self):
        self.mock_connector = MagicMock()
        self.mock_connector.health_check.return_value = True

        # Reset the module-level cache before each test
        import api.app
        api.app._connector_client = None

    def _get_client(self):
        from api.app import app
        return TestClient(app)

    def test_root_returns_healthy_when_lmstudio_up(self):
        with patch("api.app.get_connector_client", return_value=self.mock_connector):
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

        with patch("api.app.get_connector_client", return_value=self.mock_connector):
            client = self._get_client()
            response = client.get("/")

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["lmstudio"], "unhealthy")

    def test_root_returns_500_when_health_check_raises(self):
        self.mock_connector.health_check.side_effect = RuntimeError("connection refused")

        with patch("api.app.get_connector_client", return_value=self.mock_connector):
            client = self._get_client()
            response = client.get("/")

        self.assertEqual(response.status_code, 500)
        self.assertIn("Health check failed", response.json()["detail"])

    def test_health_check_called_exactly_once_per_request(self):
        with patch("api.app.get_connector_client", return_value=self.mock_connector):
            client = self._get_client()
            client.get("/")

        self.mock_connector.health_check.assert_called_once()


class TestStatusEndpoint(unittest.TestCase):
    """Verify the /api/system/status endpoint works with the lazy-initialized connector."""

    def setUp(self):
        import api.app
        api.app._connector_client = None

        self.mock_connector = MagicMock()
        self.mock_connector.health_check_async = AsyncMock(return_value=True)
        self.mock_connector.base_url = "http://localhost:1234"

    def _get_client(self):
        from api.app import app
        return TestClient(app)

    def test_status_returns_connected(self):
        with patch("api.app.get_connector_client", return_value=self.mock_connector), \
             patch("api.routers.system.get_connector_client", return_value=self.mock_connector), \
             patch("api.routers.system.task_store") as mock_store, \
             patch("api.routers.system._agent_store") as mock_agents:
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

        with patch("api.app.get_connector_client", return_value=self.mock_connector), \
             patch("api.routers.system.get_connector_client", return_value=self.mock_connector), \
             patch("api.routers.system.task_store") as mock_store, \
             patch("api.routers.system._agent_store") as mock_agents:
            mock_store.list_tasks.return_value = []
            mock_agents.list.return_value = []

            client = self._get_client()
            response = client.get("/api/system/status")

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertFalse(data["lmstudio_connected"])


class TestGetConnectorClient(unittest.TestCase):
    """Verify the lazy initialization function itself."""

    def setUp(self):
        import api.app
        api.app._connector_client = None

    def test_creates_lmstudio_by_default(self):
        from api.app import get_connector_client

        mock_connector = MagicMock()
        with patch("api.app.get_connector", return_value=mock_connector) as mock_get:
            client = get_connector_client()
            mock_get.assert_called_once_with()

        self.assertIs(client, mock_connector)

    def test_creates_cline_when_configured(self):
        from api.app import get_connector_client

        mock_connector = MagicMock()
        with patch("api.app.get_connector", return_value=mock_connector) as mock_get:
            client = get_connector_client()
            mock_get.assert_called_once_with()

        self.assertIs(client, mock_connector)

    def test_returns_cached_instance(self):
        import api.app
        from api.app import get_connector_client

        sentinel = MagicMock()
        api.app._connector_client = sentinel

        result = get_connector_client()
        self.assertIs(result, sentinel)


class TestReinitializeConnectors(unittest.TestCase):
    """Verify reinitialize_connectors resets the cached connector."""

    def setUp(self):
        import api.app
        api.app._connector_client = None

    def test_reinitialize_creates_new_connector(self):
        import api.app
        from api.app import get_connector_client, reinitialize_connectors

        mock_connector_1 = MagicMock()
        mock_connector_2 = MagicMock()

        with patch("api.app.get_connector", return_value=mock_connector_1):
            first = get_connector_client()

        # Reinitialize clears the cache
        reinitialize_connectors()

        # Next call should create a new connector
        with patch("api.app.get_connector", return_value=mock_connector_2):
            second = get_connector_client()

        self.assertIsNot(first, second)

    def test_reinitialize_switches_to_cline(self):
        import api.app
        from api.app import get_connector_client, reinitialize_connectors

        mock_connector = MagicMock()
        with patch("api.app.get_connector", return_value=mock_connector):
            reinitialize_connectors()
            client = get_connector_client()

        self.assertIs(client, mock_connector)


if __name__ == "__main__":
    unittest.main()
