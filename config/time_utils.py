"""Centralized timestamp utilities for consistent time handling across the system."""

from datetime import datetime, UTC


def get_utc_timestamp() -> str:
    return datetime.now(UTC).isoformat()


def format_relative_time(iso_timestamp: str) -> str:
    """Convert an ISO timestamp to a human-readable relative time string.
    
    Args:
        iso_timestamp: ISO format timestamp string (e.g., "2024-01-15T10:30:00+00:00")
    
    Returns:
        Relative time string like "just now", "5 minutes ago", "2 hours ago", etc.
    """
    dt = datetime.fromisoformat(iso_timestamp)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    
    now = datetime.now(UTC)
    diff = now - dt
    
    # Handle future timestamps
    if diff.total_seconds() < 0:
        return "just now"
    
    seconds = int(diff.total_seconds())
    
    if seconds < 60:
        return "just now"
    elif seconds < 3600:
        minutes = seconds // 60
        return f"{minutes} minute{'s' if minutes != 1 else ''} ago"
    elif seconds < 86400:
        hours = seconds // 3600
        return f"{hours} hour{'s' if hours != 1 else ''} ago"
    elif seconds < 604800:
        days = seconds // 86400
        return f"{days} day{'s' if days != 1 else ''} ago"
    elif seconds < 2592000:
        weeks = seconds // 604800
        return f"{weeks} week{'s' if weeks != 1 else ''} ago"
    elif seconds < 31536000:
        months = seconds // 2592000
        return f"{months} month{'s' if months != 1 else ''} ago"
    else:
        years = seconds // 31536000
        return f"{years} year{'s' if years != 1 else ''} ago"
