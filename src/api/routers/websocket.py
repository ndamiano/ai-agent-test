import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from api.websocket.manager import manager
from auth.store import resolve_token
from config.time_utils import get_utc_timestamp

logger = logging.getLogger(__name__)

router = APIRouter()


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """
    Per-user WebSocket endpoint for real-time build updates. A client receives only events for
    runs it owns — the event bus routes each event to the owning user's sockets server-side.

    HTTP middleware never sees the WebSocket scope, so the socket authenticates itself: a valid
    bearer token must ride on the `token` query param (browsers can't set headers on a WS upgrade).
    """
    user = resolve_token(websocket.query_params.get("token"))
    if user is None:
        await websocket.close(code=1008)  # policy violation
        return

    await manager.connect(websocket, user.id)

    try:
        await websocket.send_json({
            "type": "connected",
            "timestamp": get_utc_timestamp()
        })

        logger.info("WebSocket client connected")

        # Keep connection alive, accepting messages (e.g., ping/pong)
        while True:
            await websocket.receive_text()

    except WebSocketDisconnect:
        logger.info("WebSocket client disconnected")
    except Exception as e:
        logger.error("WebSocket error: %s", e)
    finally:
        manager.disconnect(websocket)
