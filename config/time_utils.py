"""Centralized timestamp utilities for consistent time handling across the system."""

from datetime import datetime


def get_utc_timestamp() -> str:
    """
    Get current UTC timestamp in ISO format.

    Returns:
        ISO 8601 formatted timestamp string (e.g., "2024-01-15T10:30:45.123456")
    """
    return datetime.utcnow().isoformat()
