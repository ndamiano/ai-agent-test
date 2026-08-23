from datetime import UTC, datetime


def get_utc_timestamp() -> str:
    return datetime.now(UTC).isoformat()
