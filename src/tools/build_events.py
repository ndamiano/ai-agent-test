"""Publish a build/spec lifecycle event: append it to the durable event log, then push it to
the per-owner WebSocket bus. The db row survives the socket; the socket is just live delivery.

An event raised inside a build carries that build's id, so a game's log reads one build at a time —
a game accumulates a build and every fix after it, and without the id they are one soup."""

import logging
from typing import Optional

from api.websocket.event_bus import event_bus
from config.time_utils import get_utc_timestamp
from db import events

logger = logging.getLogger(__name__)


def _emit(event_type: str, run_id: str, *, build_id: Optional[str] = None, **payload) -> None:
    try:
        events.record_event(run_id, event_type, payload, build_id)
    except Exception:
        logger.exception("failed to persist event %s for %s", event_type, run_id)
    try:
        event_bus.publish_sync({"type": event_type, "run_id": run_id,
                                "timestamp": get_utc_timestamp(), **payload})
    except Exception:
        logger.debug("event bus unavailable for %s", event_type)
