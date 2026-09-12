"""Server-side WS routing — an event reaches only the sockets owned by its run's owner."""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import WebSocket

from api.websocket import manager as manager_mod
from api.websocket.event_bus import EventBus
from db import games


def _own(owner):
    """Patch db ownership so every run resolves to `owner`."""
    return patch.object(games, "owner_of", lambda rid: owner)


def test_owner_of_reads_once_then_caches():
    bus = EventBus()
    reads = []

    def _counting(rid):
        reads.append(1)
        return "u1"

    with patch.object(games, "owner_of", _counting):
        assert bus._owner_of("r1") == "u1"
        assert bus._owner_of("r1") == "u1"
    assert reads == [1]                       # second lookup served from cache


def test_owner_of_none_is_not_cached():
    bus = EventBus()
    with _own(None):
        assert bus._owner_of("r1") is None    # unknown owner never poisons the cache
    assert "r1" not in bus._owner_cache


@pytest.fixture
async def sockets():
    """Two connected sockets owned by different users, plus the bus that feeds them."""
    mgr = manager_mod.manager
    ws1, ws2 = AsyncMock(spec=WebSocket), AsyncMock(spec=WebSocket)
    await mgr.connect(ws1, "u1")
    await mgr.connect(ws2, "u2")
    bus = EventBus()
    yield ws1, ws2, bus
    await bus.shutdown()
    mgr.disconnect(ws1)
    mgr.disconnect(ws2)


async def _pump(sockets, event, patcher):
    ws1, ws2, bus = sockets
    await bus.start()
    with patcher:
        await bus.queue.put(event)
        for _ in range(50):
            if ws1.send_json.await_count or ws2.send_json.await_count:
                break
            await asyncio.sleep(0.02)
        await asyncio.sleep(0.05)             # let a would-be second delivery land


@pytest.mark.anyio
async def test_run_event_reaches_only_the_owner(sockets):
    ws1, ws2, _ = sockets
    await _pump(sockets, {"type": "build_step", "run_id": "r1"}, _own("u1"))
    ws1.send_json.assert_awaited_once_with({"type": "build_step", "run_id": "r1"})
    ws2.send_json.assert_not_awaited()


@pytest.mark.anyio
async def test_event_without_run_id_broadcasts_to_everyone(sockets):
    ws1, ws2, _ = sockets
    await _pump(sockets, {"type": "server_ping"}, _own("u1"))
    ws1.send_json.assert_awaited_once()
    ws2.send_json.assert_awaited_once()


@pytest.mark.anyio
async def test_event_for_unowned_run_is_dropped(sockets):
    ws1, ws2, _ = sockets
    await _pump(sockets, {"type": "build_step", "run_id": "r1"}, _own(None))
    ws1.send_json.assert_not_awaited()
    ws2.send_json.assert_not_awaited()
