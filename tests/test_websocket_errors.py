"""Broadcast routing, disconnect cleanup, and what the endpoint logs when a socket dies."""

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import WebSocket, WebSocketDisconnect

from api.routers import websocket as websocket_module
from api.websocket.manager import ConnectionManager
from auth.store import User

pytestmark = pytest.mark.anyio


@pytest.fixture
async def two_sockets():
    manager = ConnectionManager()
    ws1, ws2 = AsyncMock(spec=WebSocket), AsyncMock(spec=WebSocket)
    await manager.connect(ws1, "u1")
    await manager.connect(ws2, "u2")
    return manager, ws1, ws2


async def test_broadcast_to_user_reaches_only_that_users_sockets(two_sockets):
    manager, ws1, ws2 = two_sockets
    await manager.broadcast_to_user("u1", {"type": "status", "run_id": "r"})

    ws1.send_json.assert_awaited_once_with({"type": "status", "run_id": "r"})
    ws2.send_json.assert_not_awaited()


async def test_broadcast_to_all_reaches_every_socket(two_sockets):
    manager, ws1, ws2 = two_sockets
    await manager.broadcast_to_all({"type": "status"})

    ws1.send_json.assert_awaited_once_with({"type": "status"})
    ws2.send_json.assert_awaited_once_with({"type": "status"})


async def test_broadcast_logs_and_disconnects_failed_client(two_sockets, caplog):
    manager, ws1, ws2 = two_sockets
    ws1.send_json = AsyncMock(side_effect=ConnectionError("gone away"))

    with caplog.at_level(logging.ERROR, logger=manager.logger.name):
        await manager.broadcast_to_all({"type": "status"})

    assert "gone away" in caplog.text
    # ws1 removed, ws2 remains reachable
    await manager.broadcast_to_user("u2", {"type": "again"})
    ws2.send_json.assert_awaited_with({"type": "again"})
    assert ws1 not in manager._user_by_ws


async def test_broadcast_to_unknown_user_is_noop(two_sockets):
    manager, ws1, ws2 = two_sockets
    await manager.broadcast_to_user("nobody", {"type": "status"})
    ws1.send_json.assert_not_awaited()
    ws2.send_json.assert_not_awaited()


async def test_disconnect_removes_connection():
    manager = ConnectionManager()
    ws = AsyncMock(spec=WebSocket)
    await manager.connect(ws, "u1")

    manager.disconnect(ws)

    assert ws not in manager._user_by_ws
    assert "u1" not in manager._sockets_by_user


async def test_disconnect_noop_for_missing_socket():
    manager = ConnectionManager()
    manager.disconnect(AsyncMock(spec=WebSocket))   # should not raise


async def _run_endpoint(receive_raise=None):
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
         patch.object(websocket_module, "resolve_token",
                      return_value=User(id="u1", handle="alice", role="user")):
        await websocket_module.websocket_endpoint(websocket)

    return websocket, logger_mock, manager_mock


async def test_endpoint_logs_on_client_disconnect():
    ws, logger_mock, manager_mock = await _run_endpoint()

    logger_mock.info.assert_called()
    manager_mock.disconnect.assert_called_with(ws)


@pytest.mark.parametrize("raised", [Exception("something went wrong"), KeyError("some-key")])
async def test_endpoint_logs_a_failed_receive(raised):
    ws, logger_mock, manager_mock = await _run_endpoint(receive_raise=raised)

    logger_mock.error.assert_called()
    manager_mock.disconnect.assert_called_with(ws)
