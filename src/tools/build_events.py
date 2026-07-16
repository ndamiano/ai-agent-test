"""Publish a build/spec lifecycle event to the per-owner WebSocket bus."""

import logging

logger = logging.getLogger(__name__)


def _emit(event_type: str, run_id: str, **payload) -> None:
    try:
        from api.websocket.event_bus import event_bus
        from config.time_utils import get_utc_timestamp
        event_bus.publish_sync({"type": event_type, "run_id": run_id,
                                "timestamp": get_utc_timestamp(), **payload})
    except Exception:
        logger.debug("event bus unavailable for %s", event_type)
