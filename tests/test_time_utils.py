from datetime import UTC, datetime, timedelta

import pytest

from config.time_utils import format_relative_time


def _make_timestamp(**kwargs):
    dt = datetime.now(UTC) - timedelta(**kwargs)
    return dt.isoformat()


@pytest.mark.parametrize(
    "delta,expected",
    [
        ({"seconds": 30}, "just now"),  # under 60 seconds
        ({"seconds": 60}, "1 minute ago"),
        ({"hours": 1}, "1 hour ago"),
        ({"days": 1}, "1 day ago"),
        ({"days": 7}, "1 week ago"),
    ],
)
def test_relative_time(delta, expected):
    ts = _make_timestamp(**delta)
    assert format_relative_time(ts) == expected


def test_future_timestamp():
    dt = datetime.now(UTC) + timedelta(hours=1)
    ts = dt.isoformat()
    assert format_relative_time(ts) == "just now"


@pytest.mark.parametrize(
    "ts",
    [
        "2024-01-15T10:30:00+00:00",
        "2024-01-15T10:30:00",
    ],
)
def test_iso_timestamps(ts):
    result = format_relative_time(ts)
    assert "ago" in result
