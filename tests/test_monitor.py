from datetime import datetime

from tools.monitor import parse_time


def test_parse_time_accepts_timestamp_without_milliseconds():
    assert parse_time("2026-10-21 21:34:28 - INFO - message") == datetime(
        2026, 10, 21, 21, 34, 28
    )


def test_parse_time_remains_compatible_with_milliseconds():
    assert parse_time("2026-04-16 18:30:10,123 - INFO - message") == datetime(
        2026, 4, 16, 18, 30, 10, 123000
    )


def test_parse_time_returns_none_for_invalid_line():
    assert parse_time("not a timestamp - INFO - message") is None
