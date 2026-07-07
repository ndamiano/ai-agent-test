import logging
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import WebSocket, WebSocketDisconnect

from api.websocket.manager import ConnectionManager


class TestConnectionManagerBroadcast(unittest.IsolatedAsyncioTestCase):
    """Broadcast routing + exception handling."""

    async def asyncSetUp(self):
        self.manager = ConnectionManager()
        self.ws1 = AsyncMock(spec=WebSocket)   # user u1
        self.ws2 = AsyncMock(spec=WebSocket)   # user u2
        await self.manager.connect(self.ws1, "u1")
        await self.manager.connect(self.ws2, "u2")

    async def test_broadcast_to_user_reaches_only_that_users_sockets(self):
        await self.manager.broadcast_to_user("u1", {"type": "status", "run_id": "r"})

        self.ws1.send_json.assert_awaited_once_with({"type": "status", "run_id": "r"})
        self.ws2.send_json.assert_not_awaited()

    async def test_broadcast_to_all_reaches_every_socket(self):
        await self.manager.broadcast_to_all({"type": "status"})

        self.ws1.send_json.assert_awaited_once_with({"type": "status"})
        self.ws2.send_json.assert_awaited_once_with({"type": "status"})

    async def test_broadcast_logs_and_disconnects_failed_client(self):
        self.ws1.send_json = AsyncMock(side_effect=ConnectionError("gone away"))

        with self.assertLogs(self.manager.logger, level=logging.ERROR) as cm:
            await self.manager.broadcast_to_all({"type": "status"})

        self.assertIn("gone away", cm.output[0])
        # ws1 removed, ws2 remains reachable
        await self.manager.broadcast_to_user("u2", {"type": "again"})
        self.ws2.send_json.assert_awaited_with({"type": "again"})
        self.assertNotIn(self.ws1, self.manager._user_by_ws)

    async def test_broadcast_to_unknown_user_is_noop(self):
        await self.manager.broadcast_to_user("nobody", {"type": "status"})
        self.ws1.send_json.assert_not_awaited()
        self.ws2.send_json.assert_not_awaited()


class TestConnectionManagerDisconnect(unittest.IsolatedAsyncioTestCase):
    """disconnect cleans both indexes."""

    async def test_disconnect_removes_connection(self):
        manager = ConnectionManager()
        ws = AsyncMock(spec=WebSocket)
        await manager.connect(ws, "u1")

        manager.disconnect(ws)

        self.assertNotIn(ws, manager._user_by_ws)
        self.assertNotIn("u1", manager._sockets_by_user)

    async def test_disconnect_noop_for_missing_socket(self):
        manager = ConnectionManager()
        ws = AsyncMock(spec=WebSocket)
        manager.disconnect(ws)  # should not raise


class TestWebSocketEndpointLogging(unittest.IsolatedAsyncioTestCase):
    """Tests that websocket_endpoint logs errors in its catch block."""

    async def _run_endpoint(self, receive_raise=None):
        from api.routers import websocket as websocket_module
        from auth.store import User

        websocket = AsyncMock(spec=WebSocket)
        websocket.query_params = {"token": "valid"}
        websocket.send_json = AsyncMock()
        websocket.receive_text = AsyncMock(
            side_effect=receive_raise or WebSocketDisconnect(code=1000)
        )

        manager_mock = AsyncMock()
        manager_mock.connect = AsyncMock()
        manager_mock.disconnect = MagicMock()

        logger_mock = MagicMock()

        with patch.object(websocket_module, "manager", manager_mock), \
             patch.object(websocket_module, "logger", logger_mock), \
             patch("auth.store.resolve_token",
                   return_value=User(id="u1", handle="alice", role="user")):
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
