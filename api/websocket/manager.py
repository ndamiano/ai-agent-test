import asyncio
from typing import Set
from fastapi import WebSocket
import logging

class ConnectionManager:
    def __init__(self):
        self.active_connections: Set[WebSocket] = set()
        self.logger = logging.getLogger(__name__)

    async def connect(self, websocket: WebSocket) -> None:
        """Accept the connection and register it globally."""
        await websocket.accept()
        self.active_connections.add(websocket)
        self.logger.info("WebSocket connected (total: %d)", len(self.active_connections))

    def disconnect(self, websocket: WebSocket) -> None:
        """Remove the connection."""
        self.active_connections.discard(websocket)
        self.logger.info("WebSocket disconnected (total: %d)", len(self.active_connections))

    async def broadcast_to_all(self, message: dict) -> None:
        """Send a JSON message to all connected clients. Handles disconnected clients gracefully."""
        if not self.active_connections:
            return

        disconnected = []
        send_tasks = []

        for websocket in list(self.active_connections):
            send_tasks.append((websocket, asyncio.create_task(websocket.send_json(message))))

        for websocket, task in send_tasks:
            try:
                await task
            except Exception as e:
                self.logger.error("WebSocket broadcast error: %s", e)
                disconnected.append(websocket)

        for websocket in disconnected:
            self.disconnect(websocket)

manager = ConnectionManager()
