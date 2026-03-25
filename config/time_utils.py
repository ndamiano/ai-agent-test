"""Centralized timestamp utilities for consistent time handling across the system."""

from datetime import datetime, UTC


def get_utc_timestamp() -> str:
    return datetime.now(UTC).isoformat()
