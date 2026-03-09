from typing import Dict, List, Set
from fastapi import WebSocket
import logging

class WebSocketManager:
    def __init__(self):
        self.active_connections: Dict[str, Set[WebSocket]] = {}
        self.logger = logging.getLogger(__name__)

    async def connect(self, websocket: WebSocket, agent_id: str):
        """Connect a new WebSocket client."""
        await websocket.accept()
        if agent_id not in self.active_connections:
            self.active_connections[agent_id] = set()
        self.active_connections[agent_id].add(websocket)
        self.logger.info(f"WebSocket connected for agent: {agent_id}")

    def disconnect(self, websocket: WebSocket, agent_id: str):
        """Disconnect a WebSocket client."""
        if agent_id in self.active_connections:
            self.active_connections[agent_id].discard(websocket)
            if not self.active_connections[agent_id]:
                del self.active_connections[agent_id]
        self.logger.info(f"WebSocket disconnected for agent: {agent_id}")

    async def send_message(self, message: str, agent_id: str):
        """Send a message to all clients connected to an agent."""
        if agent_id in self.active_connections:
            disconnected = set()
            for websocket in self.active_connections[agent_id]:
                try:
                    await websocket.send_text(message)
                except:
                    disconnected.add(websocket)
            for websocket in disconnected:
                self.disconnect(websocket, agent_id)

    async def broadcast(self, message: str):
        """Broadcast a message to all connected clients."""
        for agent_id, connections in self.active_connections.items():
            await self.send_message(message, agent_id)