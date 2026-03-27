import logging
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import WebSocket, WebSocketDisconnect

from api.websocket.manager import ConnectionManager


class TestConnectionManagerBroadcast(unittest.IsolatedAsyncioTestCase):
    """Tests for ConnectionManager.broadcast exception handling."""

    async def asyncSetUp(self):
        self.manager = ConnectionManager()
        self.ws1 = AsyncMock(spec=WebSocket)
        self.ws2 = AsyncMock(spec=WebSocket)

    async def test_broadcast_sends_to_all_connections(self):
        self.manager.active_connections["task-1"] = [self.ws1, self.ws2]
        await self.manager.broadcast("task-1", {"type": "status"})

        self.ws1.send_json.assert_awaited_once_with({"type": "status"})
        self.ws2.send_json.assert_awaited_once_with({"type": "status"})

    async def test_broadcast_logs_and_disconnects_failed_client(self):
        self.manager.active_connections["task-1"] = [self.ws1, self.ws2]
        self.ws1.send_json = AsyncMock(side_effect=ConnectionError("gone away"))

        with self.assertLogs(self.manager.logger, level=logging.ERROR) as cm:
            await self.manager.broadcast("task-1", {"type": "status"})

        self.assertIn("gone away", cm.output[0])
        self.assertIn("task-1", cm.output[0])
        # ws1 removed, ws2 remains
        self.assertEqual(self.manager.active_connections["task-1"], [self.ws2])

    async def test_broadcast_removes_key_when_all_clients_fail(self):
        self.ws1.send_json = AsyncMock(side_effect=ConnectionError("fail"))
        self.ws2.send_json = AsyncMock(side_effect=ConnectionError("fail"))
        self.manager.active_connections["task-1"] = [self.ws1, self.ws2]

        with self.assertLogs(self.manager.logger, level=logging.ERROR):
            await self.manager.broadcast("task-1", {"type": "status"})

        self.assertIsNone(self.manager.active_connections.get("task-1"))

    async def test_broadcast_noop_for_unknown_task(self):
        await self.manager.broadcast("nonexistent", {"type": "status"})


class TestConnectionManagerDisconnect(unittest.IsolatedAsyncioTestCase):
    """Tests for ConnectionManager.disconnect cleanup."""

    async def test_disconnect_removes_connection(self):
        manager = ConnectionManager()
        ws = AsyncMock(spec=WebSocket)
        manager.active_connections["task-1"] = [ws]

        manager.disconnect("task-1", ws)

        self.assertNotIn("task-1", manager.active_connections)

    async def test_disconnect_noop_for_missing_task(self):
        manager = ConnectionManager()
        ws = AsyncMock(spec=WebSocket)
        manager.disconnect("nonexistent", ws)


class TestWebSocketEndpointLogging(unittest.IsolatedAsyncioTestCase):
    """Tests that websocket_endpoint logs errors in its catch block."""

    async def _run_endpoint(self, task_id, receive_raise=None, task_return=None,
                            get_task_raise=None):
        from api.routers import tasks as tasks_module

        websocket = AsyncMock(spec=WebSocket)
        websocket.send_json = AsyncMock()
        websocket.receive_text = AsyncMock(
            side_effect=receive_raise or WebSocketDisconnect(code=1000)
        )

        manager_mock = AsyncMock()
        manager_mock.connect = AsyncMock()
        manager_mock.disconnect = MagicMock()

        store_mock = MagicMock()
        if get_task_raise is not None:
            store_mock.get_task.side_effect = get_task_raise
        else:
            store_mock.get_task.return_value = task_return or {"status": "active"}

        logger_mock = MagicMock()

        with patch.object(tasks_module, "manager", manager_mock), \
             patch.object(tasks_module, "task_store", store_mock), \
             patch.object(tasks_module, "logger", logger_mock):
            await tasks_module.websocket_endpoint(websocket, task_id)

        return websocket, logger_mock, manager_mock

    async def test_endpoint_logs_on_client_disconnect(self):
        ws, logger_mock, manager_mock = await self._run_endpoint("task-1")

        logger_mock.error.assert_called()
        fmt, task_id_arg, exc_arg = logger_mock.error.call_args[0]
        self.assertEqual(task_id_arg, "task-1")
        manager_mock.disconnect.assert_called_with("task-1", ws)

    async def test_endpoint_sends_error_when_task_not_found(self):
        ws, logger_mock, manager_mock = await self._run_endpoint(
            "task-1",
            get_task_raise=Exception("task not found in store"),
        )

        logger_mock.error.assert_called()
        ws.send_json.assert_awaited_once()
        sent = ws.send_json.call_args[0][0]
        self.assertEqual(sent["type"], "error")
        self.assertIn("not found", sent["message"])
        manager_mock.disconnect.assert_called_with("task-1", ws)

    async def test_endpoint_logs_generic_error_without_sending(self):
        ws, logger_mock, manager_mock = await self._run_endpoint(
            "task-1",
            get_task_raise=KeyError("task-1"),
        )

        logger_mock.error.assert_called()
        ws.send_json.assert_not_awaited()
        manager_mock.disconnect.assert_called_with("task-1", ws)
