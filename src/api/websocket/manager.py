import asyncio
from typing import Dict, Iterable, Optional, Set
from fastapi import WebSocket
import logging

class ConnectionManager:
    """Tracks live sockets keyed to the owning user, so an event routes only to that user's
    connections. A user may hold several sockets (multiple tabs); the reverse index fans to all."""

    def __init__(self):
        self._user_by_ws: Dict[WebSocket, str] = {}
        self._sockets_by_user: Dict[str, Set[WebSocket]] = {}
        self.logger = logging.getLogger(__name__)

    async def connect(self, websocket: WebSocket, user_id: str) -> None:
        """Accept the connection and register it under its authenticated user."""
        await websocket.accept()
        self._user_by_ws[websocket] = user_id
        self._sockets_by_user.setdefault(user_id, set()).add(websocket)
        self.logger.info("WebSocket connected user=%s (total: %d)", user_id, len(self._user_by_ws))

    def disconnect(self, websocket: WebSocket) -> None:
        """Remove the connection from both indexes."""
        user_id = self._user_by_ws.pop(websocket, None)
        if user_id is not None:
            socks = self._sockets_by_user.get(user_id)
            if socks is not None:
                socks.discard(websocket)
                if not socks:
                    self._sockets_by_user.pop(user_id, None)
        self.logger.info("WebSocket disconnected (total: %d)", len(self._user_by_ws))

    async def broadcast_to_user(self, user_id: str, message: dict) -> None:
        """Send to every socket owned by one user (nothing if they have none open)."""
        await self._send_many(self._sockets_by_user.get(user_id), message)

    async def broadcast_to_all(self, message: dict) -> None:
        """Send to every connected socket — only for events with no owning run/user."""
        await self._send_many(list(self._user_by_ws.keys()), message)

    async def _send_many(self, sockets: Optional[Iterable[WebSocket]], message: dict) -> None:
        if not sockets:
            return
        send_tasks = [(ws, asyncio.create_task(ws.send_json(message))) for ws in list(sockets)]
        for websocket, task in send_tasks:
            try:
                await task
            except Exception as e:
                self.logger.error("WebSocket send error: %s", e)
                self.disconnect(websocket)

manager = ConnectionManager()
