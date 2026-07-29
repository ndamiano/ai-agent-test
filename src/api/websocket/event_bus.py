import asyncio
import logging
from typing import Optional

from api.websocket.manager import manager
from db import store as db_store

logger = logging.getLogger(__name__)


class EventBus:
    """
    Global event bus for publishing WebSocket events from anywhere in the codebase.

    Uses asyncio.Queue to bridge sync (background threads) and async (FastAPI) contexts.
    A background task consumes the queue and routes each event to the sockets of the user who
    owns its run; an event with no run_id goes to every socket.
    """

    def __init__(self):
        self.queue: Optional[asyncio.Queue] = None
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self.consumer_task: Optional[asyncio.Task] = None
        self._shutdown = False
        self.logger = logging.getLogger(__name__)
        # run_id -> owning user id. Ownership is write-once, so a cached owner never goes stale.
        self._owner_cache: dict = {}

    def _owner_of(self, run_id: str) -> Optional[str]:
        owner = self._owner_cache.get(run_id)
        if owner is None:
            try:
                owner = db_store.owner_of(run_id)
            except Exception:
                owner = None
            if owner is not None:
                self._owner_cache[run_id] = owner
        return owner

    async def start(self) -> None:
        self.loop = asyncio.get_running_loop()
        self.queue = asyncio.Queue(maxsize=1000)
        self._shutdown = False
        self.consumer_task = asyncio.create_task(self._consume_events())
        self.logger.info("Event bus started")

    async def shutdown(self) -> None:
        self._shutdown = True
        if self.consumer_task:
            self.consumer_task.cancel()
            try:
                await self.consumer_task
            except asyncio.CancelledError:
                pass
        self.logger.info("Event bus stopped")

    def publish_sync(self, event: dict) -> None:
        """Thread-safe publish from sync/background thread contexts."""
        if self.loop is None or self.queue is None:
            self.logger.warning("Event bus not started, dropping event: %s", event.get('type'))
            return

        try:
            future = asyncio.run_coroutine_threadsafe(
                self.queue.put(event),
                self.loop
            )
            future.result(timeout=1.0)
        except TimeoutError:
            self.logger.error("Failed to publish event (timeout): %s", event.get('type'))
        except Exception as e:
            self.logger.error("Failed to publish event: %s - %s", event.get('type'), e)

    async def _consume_events(self) -> None:
        self.logger.info("Event consumer started")

        try:
            while not self._shutdown:
                try:
                    event = await asyncio.wait_for(self.queue.get(), timeout=0.1)
                    run_id = event.get("run_id")
                    if run_id is None:
                        await manager.broadcast_to_all(event)
                    else:
                        owner = self._owner_of(run_id)
                        if owner is None:
                            self.logger.debug("dropping event for unowned run %s", run_id)
                        else:
                            await manager.broadcast_to_user(owner, event)
                except asyncio.TimeoutError:
                    continue
                except Exception as e:
                    self.logger.error("Error consuming event: %s", e)
        except asyncio.CancelledError:
            self.logger.info("Event consumer cancelled")
            raise


event_bus = EventBus()
