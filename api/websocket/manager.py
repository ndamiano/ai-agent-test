from typing import Dict, List, Set
from fastapi import WebSocket
import logging

class ConnectionManager:
    def __init__(self):
        self.active_connections: Dict[str, List[WebSocket]] = {}
        self.logger = logging.getLogger(__name__)

    async def connect(self, task_id: str, websocket: WebSocket) -> None:
        """Accept the connection and register it for the given task_id."""
        await websocket.accept()
        if task_id not in self.active_connections:
            self.active_connections[task_id] = []
        self.active_connections[task_id].append(websocket)
        self.logger.info(f"WebSocket connected for task: {task_id}")

    def disconnect(self, task_id: str, websocket: WebSocket) -> None:
        """Remove the connection and clean up empty task entries."""
        if task_id in self.active_connections:
            if websocket in self.active_connections[task_id]:
                self.active_connections[task_id].remove(websocket)
                if not self.active_connections[task_id]:
                    del self.active_connections[task_id]
        self.logger.info(f"WebSocket disconnected for task: {task_id}")

    def broadcast(self, task_id: str, message: dict) -> None:
        """Send a JSON message to all connections for a task. Handles disconnected clients gracefully."""
        if task_id in self.active_connections:
            disconnected = []
            for websocket in self.active_connections[task_id]:
                try:
                    # Get or create event loop for async operations
                    import asyncio
                    try:
                        loop = asyncio.get_event_loop()
                        if loop.is_running():
                            # If loop is already running, schedule the coroutine
                            asyncio.ensure_future(websocket.send_json(message))
                        else:
                            # If no loop is running, run it
                            loop.run_until_complete(websocket.send_json(message))
                    except RuntimeError:
                        # No event loop in this thread, create one
                        asyncio.run(websocket.send_json(message))
                except:
                    disconnected.append(websocket)
            for websocket in disconnected:
                self.disconnect(task_id, websocket)

    async def broadcast_all(self, message: dict) -> None:
        """Broadcast to all connected clients across all tasks. Used for system-level events."""
        for task_id, connections in self.active_connections.items():
            await self.broadcast(task_id, message)

manager = ConnectionManager()
