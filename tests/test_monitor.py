from datetime import datetime

import tools.monitor as monitor
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


def test_get_last_line_reads_final_line(tmp_path, monkeypatch):
    log_file = tmp_path / "bot.log"
    log_file.write_text("first line\nlast line\n", encoding="utf-8")
    monkeypatch.setattr(monitor, "LOG_FILE", str(log_file))

    assert monitor.get_last_line() == "last line"


def test_get_last_line_handles_empty_file(tmp_path, monkeypatch):
    log_file = tmp_path / "bot.log"
    log_file.touch()
    monkeypatch.setattr(monitor, "LOG_FILE", str(log_file))

    assert monitor.get_last_line() is None


def test_get_last_line_handles_file_without_trailing_newline(tmp_path, monkeypatch):
    log_file = tmp_path / "bot.log"
    log_file.write_text("first line\nlast line", encoding="utf-8")
    monkeypatch.setattr(monitor, "LOG_FILE", str(log_file))

    assert monitor.get_last_line() == "last line"


def test_get_last_line_skips_large_earlier_content(tmp_path, monkeypatch):
    log_file = tmp_path / "bot.log"
    log_file.write_bytes(b"x" * (monitor.LOG_READ_CHUNK_SIZE * 8) + b"\nlast line\n")
    monkeypatch.setattr(monitor, "LOG_FILE", str(log_file))

    assert monitor.get_last_line() == "last line"


def test_monitor_compacts_match_ids_like_console_log_output():
    last_line = (
        "2026-10-21 17:32:04,672 - INFO - "
        "Matches updated: 2 (3083298, 3021642)"
    )

    assert monitor.format_console_log_message(last_line) == (
        "2026-10-21 17:32:04,672 - INFO - Matches updated: 2"
    )


def test_monitor_compacts_synchronized_match_counts():
    last_line = (
        "2026-10-21 17:32:04 - INFO - Matches synchronized: "
        "Active 2 (3083298, 3021642) New 1 (3021642) Removed 0 (-)"
    )

    assert monitor.format_console_log_message(last_line) == (
        "2026-10-21 17:32:04 - INFO - Matches synchronized: "
        "Active 2 New 1 Removed 0"
    )
