"""Publish a build/spec lifecycle event: append it to the durable event log, then push it to
the per-owner WebSocket bus. The db row survives the socket; the socket is just live delivery."""

import logging

from api.websocket.event_bus import event_bus
from config.time_utils import get_utc_timestamp
from db import store as db_store

logger = logging.getLogger(__name__)


def _emit(event_type: str, run_id: str, **payload) -> None:
    try:
        db_store.record_event(run_id, event_type, payload)
    except Exception:
        logger.exception("failed to persist event %s for %s", event_type, run_id)
    try:
        event_bus.publish_sync({"type": event_type, "run_id": run_id,
                                "timestamp": get_utc_timestamp(), **payload})
    except Exception:
        logger.debug("event bus unavailable for %s", event_type)
