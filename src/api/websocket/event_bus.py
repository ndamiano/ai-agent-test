import asyncio
import logging
from typing import Optional

logger = logging.getLogger(__name__)


class EventBus:
    """
    Global event bus for publishing WebSocket events from anywhere in the codebase.

    Uses asyncio.Queue to bridge sync (background threads) and async (FastAPI) contexts.
    Events are consumed by a background task and broadcast to all WebSocket connections.
    """

    def __init__(self):
        self.queue: Optional[asyncio.Queue] = None
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self.consumer_task: Optional[asyncio.Task] = None
        self._shutdown = False
        self.logger = logging.getLogger(__name__)

    async def start(self) -> None:
        """Initialize the event bus and start consuming events."""
        self.loop = asyncio.get_running_loop()
        self.queue = asyncio.Queue(maxsize=1000)
        self._shutdown = False
        self.consumer_task = asyncio.create_task(self._consume_events())
        self.logger.info("Event bus started")

    async def shutdown(self) -> None:
        """Stop the event bus and drain remaining events."""
        self._shutdown = True
        if self.consumer_task:
            self.consumer_task.cancel()
            try:
                await self.consumer_task
            except asyncio.CancelledError:
                pass
        self.logger.info("Event bus stopped")

    async def publish(self, event: dict) -> None:
        """
        Publish an event from an async context.

        Args:
            event: Event dictionary containing at minimum 'type' and 'task_id' keys
        """
        if self.queue is None:
            self.logger.warning("Event bus not started, dropping event: %s", event.get('type'))
            return

        try:
            await self.queue.put(event)
        except asyncio.QueueFull:
            self.logger.error("Event queue full, dropping event: %s", event.get('type'))

    def publish_sync(self, event: dict) -> None:
        """
        Thread-safe publish from sync/background thread contexts.

        Args:
            event: Event dictionary containing at minimum 'type' and 'task_id' keys
        """
        if self.loop is None or self.queue is None:
            self.logger.warning("Event bus not started, dropping event: %s", event.get('type'))
            return

        try:
            # Schedule the coroutine on the event loop from another thread
            future = asyncio.run_coroutine_threadsafe(
                self.queue.put(event),
                self.loop
            )
            # Wait with timeout to prevent hanging
            future.result(timeout=1.0)
        except TimeoutError:
            self.logger.error("Failed to publish event (timeout): %s", event.get('type'))
        except Exception as e:
            self.logger.error("Failed to publish event: %s - %s", event.get('type'), e)

    async def _consume_events(self) -> None:
        """Background task that consumes events from queue and broadcasts to WebSockets."""
        from api.websocket.manager import manager

        self.logger.info("Event consumer started")

        try:
            while not self._shutdown:
                try:
                    event = await asyncio.wait_for(self.queue.get(), timeout=0.1)
                    await manager.broadcast_to_all(event)
                except asyncio.TimeoutError:
                    continue
                except Exception as e:
                    self.logger.error("Error consuming event: %s", e)
        except asyncio.CancelledError:
            self.logger.info("Event consumer cancelled")
            raise


# Global singleton instance
event_bus = EventBus()
