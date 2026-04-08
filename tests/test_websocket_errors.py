import logging
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import WebSocket, WebSocketDisconnect

from api.websocket.manager import ConnectionManager


class TestConnectionManagerBroadcast(unittest.IsolatedAsyncioTestCase):
    """Tests for ConnectionManager.broadcast_to_all exception handling."""

    async def asyncSetUp(self):
        self.manager = ConnectionManager()
        self.ws1 = AsyncMock(spec=WebSocket)
        self.ws2 = AsyncMock(spec=WebSocket)

    async def test_broadcast_sends_to_all_connections(self):
        self.manager.active_connections = {self.ws1, self.ws2}
        await self.manager.broadcast_to_all({"type": "status", "task_id": "task-1"})

        self.ws1.send_json.assert_awaited_once_with({"type": "status", "task_id": "task-1"})
        self.ws2.send_json.assert_awaited_once_with({"type": "status", "task_id": "task-1"})

    async def test_broadcast_logs_and_disconnects_failed_client(self):
        self.manager.active_connections = {self.ws1, self.ws2}
        self.ws1.send_json = AsyncMock(side_effect=ConnectionError("gone away"))

        with self.assertLogs(self.manager.logger, level=logging.ERROR) as cm:
            await self.manager.broadcast_to_all({"type": "status", "task_id": "task-1"})

        self.assertIn("gone away", cm.output[0])
        # ws1 removed, ws2 remains
        self.assertIn(self.ws2, self.manager.active_connections)
        self.assertNotIn(self.ws1, self.manager.active_connections)

    async def test_broadcast_removes_key_when_all_clients_fail(self):
        self.ws1.send_json = AsyncMock(side_effect=ConnectionError("fail"))
        self.ws2.send_json = AsyncMock(side_effect=ConnectionError("fail"))
        self.manager.active_connections = {self.ws1, self.ws2}

        with self.assertLogs(self.manager.logger, level=logging.ERROR):
            await self.manager.broadcast_to_all({"type": "status", "task_id": "task-1"})

        # Both should be removed
        self.assertEqual(len(self.manager.active_connections), 0)

    async def test_broadcast_noop_for_unknown_task(self):
        # Empty connections - should not raise
        await self.manager.broadcast_to_all({"type": "status", "task_id": "nonexistent"})


class TestConnectionManagerDisconnect(unittest.IsolatedAsyncioTestCase):
    """Tests for ConnectionManager.disconnect cleanup."""

    async def test_disconnect_removes_connection(self):
        manager = ConnectionManager()
        ws = AsyncMock(spec=WebSocket)
        manager.active_connections = {ws}

        manager.disconnect(ws)

        self.assertNotIn(ws, manager.active_connections)

    async def test_disconnect_noop_for_missing_task(self):
        manager = ConnectionManager()
        ws = AsyncMock(spec=WebSocket)
        # Should not raise even if ws not in set
        manager.disconnect(ws)


class TestWebSocketEndpointLogging(unittest.IsolatedAsyncioTestCase):
    """Tests that websocket_endpoint logs errors in its catch block."""

    async def _run_endpoint(self, receive_raise=None):
        from api.routers import websocket as websocket_module

        websocket = AsyncMock(spec=WebSocket)
        websocket.send_json = AsyncMock()
        websocket.receive_text = AsyncMock(
            side_effect=receive_raise or WebSocketDisconnect(code=1000)
        )

        manager_mock = AsyncMock()
        manager_mock.connect = AsyncMock()
        manager_mock.disconnect = MagicMock()

        logger_mock = MagicMock()

        with patch.object(websocket_module, "manager", manager_mock), \
             patch.object(websocket_module, "logger", logger_mock):
            await websocket_module.websocket_endpoint(websocket)

        return websocket, logger_mock, manager_mock

    async def test_endpoint_logs_on_client_disconnect(self):
        ws, logger_mock, manager_mock = await self._run_endpoint()

        logger_mock.info.assert_called()
        manager_mock.disconnect.assert_called_with(ws)

    async def test_endpoint_sends_error_when_task_not_found(self):
        # With the new global websocket, task-not-found errors are not sent from the endpoint
        # They are handled by the client filtering messages by task_id
        ws, logger_mock, manager_mock = await self._run_endpoint(
            receive_raise=Exception("something went wrong"),
        )

        logger_mock.error.assert_called()
        manager_mock.disconnect.assert_called_with(ws)

    async def test_endpoint_logs_generic_error_without_sending(self):
        ws, logger_mock, manager_mock = await self._run_endpoint(
            receive_raise=KeyError("some-key"),
        )

        logger_mock.error.assert_called()
        manager_mock.disconnect.assert_called_with(ws)
