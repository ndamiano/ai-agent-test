import unittest
from datetime import datetime, timedelta, UTC
from config.time_utils import format_relative_time


class TestFormatRelativeTime(unittest.TestCase):
    def _make_timestamp(self, **kwargs):
        dt = datetime.now(UTC) - timedelta(**kwargs)
        return dt.isoformat()

    def test_just_now_under_60_seconds(self):
        ts = self._make_timestamp(seconds=30)
        self.assertEqual(format_relative_time(ts), "just now")

    def test_single_minute(self):
        ts = self._make_timestamp(seconds=60)
        self.assertEqual(format_relative_time(ts), "1 minute ago")

    def test_single_hour(self):
        ts = self._make_timestamp(hours=1)
        self.assertEqual(format_relative_time(ts), "1 hour ago")

    def test_single_day(self):
        ts = self._make_timestamp(days=1)
        self.assertEqual(format_relative_time(ts), "1 day ago")

    def test_single_week(self):
        ts = self._make_timestamp(days=7)
        self.assertEqual(format_relative_time(ts), "1 week ago")

    def test_future_timestamp(self):
        dt = datetime.now(UTC) + timedelta(hours=1)
        ts = dt.isoformat()
        self.assertEqual(format_relative_time(ts), "just now")

    def test_iso_with_timezone(self):
        ts = "2024-01-15T10:30:00+00:00"
        result = format_relative_time(ts)
        self.assertIn("ago", result)

    def test_iso_without_timezone(self):
        ts = "2024-01-15T10:30:00"
        result = format_relative_time(ts)
        self.assertIn("ago", result)


if __name__ == "__main__":
    unittest.main()