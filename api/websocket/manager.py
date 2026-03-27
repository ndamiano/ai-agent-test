import asyncio
from typing import Dict, List
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

    async def broadcast(self, task_id: str, message: dict) -> None:
        """Send a JSON message to all connections for a task. Handles disconnected clients gracefully."""
        if task_id not in self.active_connections:
            return
        disconnected = []
        send_tasks = []
        for websocket in self.active_connections[task_id]:
            send_tasks.append((websocket, asyncio.create_task(websocket.send_json(message))))
        for websocket, task in send_tasks:
            try:
                await task
            except Exception as e:
                self.logger.error(
                    "WebSocket broadcast error for task %s: %s", task_id, e
                )
                disconnected.append(websocket)
        for websocket in disconnected:
            self.disconnect(task_id, websocket)

    def broadcast_sync(self, task_id: str, message: dict) -> None:
        """Thread-safe sync wrapper for broadcast — usable from background threads."""
        try:
            loop = asyncio.get_running_loop()
            asyncio.run_coroutine_threadsafe(self.broadcast(task_id, message), loop)
        except RuntimeError:
            # No running loop; fallback to a new one
            asyncio.run(self.broadcast(task_id, message))

    async def broadcast_all(self, message: dict) -> None:
        """Broadcast to all connected clients across all tasks. Used for system-level events."""
        for task_id in list(self.active_connections.keys()):
            await self.broadcast(task_id, message)

manager = ConnectionManager()
