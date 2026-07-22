"""Server-side WS routing — an event reaches only the sockets owned by its run's owner."""

import asyncio
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi import WebSocket

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from api.websocket import manager as manager_mod
from api.websocket.event_bus import EventBus
from db import store as db_store


def _own(owner):
    """Patch db ownership so every run resolves to `owner`."""
    return patch.object(db_store, "owner_of", lambda rid: owner)


class TestOwnerCache(unittest.TestCase):
    def test_owner_of_reads_once_then_caches(self):
        bus = EventBus()
        reads = []

        def _counting(rid):
            reads.append(1)
            return "u1"

        with patch.object(db_store, "owner_of", _counting):
            assert bus._owner_of("r1") == "u1"
            assert bus._owner_of("r1") == "u1"
        assert reads == [1]                       # second lookup served from cache

    def test_owner_of_none_is_not_cached(self):
        bus = EventBus()
        with _own(None):
            assert bus._owner_of("r1") is None    # unknown owner never poisons the cache
        assert "r1" not in bus._owner_cache


class TestEventRouting(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.mgr = manager_mod.manager
        self.ws1 = AsyncMock(spec=WebSocket)      # u1
        self.ws2 = AsyncMock(spec=WebSocket)      # u2
        await self.mgr.connect(self.ws1, "u1")
        await self.mgr.connect(self.ws2, "u2")
        self.bus = EventBus()

    async def asyncTearDown(self):
        await self.bus.shutdown()
        self.mgr.disconnect(self.ws1)
        self.mgr.disconnect(self.ws2)

    async def _pump(self, event, patcher):
        await self.bus.start()
        with patcher:
            await self.bus.queue.put(event)
            for _ in range(50):
                if self.ws1.send_json.await_count or self.ws2.send_json.await_count:
                    break
                await asyncio.sleep(0.02)
            await asyncio.sleep(0.05)             # let a would-be second delivery land

    async def test_run_event_reaches_only_the_owner(self):
        await self._pump({"type": "build_step", "run_id": "r1"}, _own("u1"))
        self.ws1.send_json.assert_awaited_once_with({"type": "build_step", "run_id": "r1"})
        self.ws2.send_json.assert_not_awaited()

    async def test_event_without_run_id_broadcasts_to_everyone(self):
        await self._pump({"type": "server_ping"}, _own("u1"))
        self.ws1.send_json.assert_awaited_once()
        self.ws2.send_json.assert_awaited_once()

    async def test_event_for_unowned_run_is_dropped(self):
        await self._pump({"type": "build_step", "run_id": "r1"}, _own(None))
        self.ws1.send_json.assert_not_awaited()
        self.ws2.send_json.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
