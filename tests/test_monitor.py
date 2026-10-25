from datetime import datetime, timedelta

import pytest
import app.tools.monitor as monitor
from app import config
from app.tools.monitor import parse_time


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


@pytest.mark.parametrize("line", [None, "", 123])
def test_parse_time_returns_none_for_empty_or_malformed_values(line):
    assert parse_time(line) is None


def test_get_last_line_reads_final_line(tmp_path, monkeypatch):
    log_file = tmp_path / "bot.log"
    log_file.write_text("first line\nlast line\n", encoding="utf-8")
    monkeypatch.setattr(config, "BOT_LOG_FILE", log_file)

    assert monitor.get_last_line() == "last line"


def test_get_last_line_handles_empty_file(tmp_path, monkeypatch):
    log_file = tmp_path / "bot.log"
    log_file.touch()
    monkeypatch.setattr(config, "BOT_LOG_FILE", log_file)

    assert monitor.get_last_line() is None


def test_get_last_line_handles_file_without_trailing_newline(tmp_path, monkeypatch):
    log_file = tmp_path / "bot.log"
    log_file.write_text("first line\nlast line", encoding="utf-8")
    monkeypatch.setattr(config, "BOT_LOG_FILE", log_file)

    assert monitor.get_last_line() == "last line"


def test_get_last_line_skips_large_earlier_content(tmp_path, monkeypatch):
    log_file = tmp_path / "bot.log"
    log_file.write_bytes(
        b"x" * (config.MONITOR_LOG_READ_CHUNK_SIZE * 8) + b"\nlast line\n"
    )
    monkeypatch.setattr(config, "BOT_LOG_FILE", log_file)

    assert monitor.get_last_line() == "last line"


def test_get_last_line_ignores_invalid_utf8_bytes(tmp_path, monkeypatch):
    log_file = tmp_path / "bot.log"
    log_file.write_bytes(b"last\xffline\n")
    monkeypatch.setattr(config, "BOT_LOG_FILE", log_file)

    assert monitor.get_last_line() == "lastline"


def test_get_last_line_returns_none_and_logs_when_file_is_missing(
    tmp_path, monkeypatch, caplog
):
    monkeypatch.setattr(config, "BOT_LOG_FILE", tmp_path / "missing.log")

    assert monitor.get_last_line() is None
    assert "Error reading bot log" in caplog.text


@pytest.mark.parametrize(
    ("response_status", "expected"),
    [(200, True), (503, False)],
)
def test_send_posts_telegram_status_and_handles_http_result(
    monkeypatch, caplog, response_status, expected
):
    calls = []

    class Response:
        status_code = response_status
        text = "service unavailable"

    monkeypatch.setattr(config, "MONITOR_TELEGRAM_TOKEN", "test-token")
    monkeypatch.setattr(config, "MONITOR_CHAT_ID", "test-chat")
    monkeypatch.setattr(monitor, "TELEGRAM_PROXIES", {"https": "proxy"})
    monkeypatch.setattr(
        monitor.requests,
        "post",
        lambda *args, **kwargs: calls.append((args, kwargs)) or Response(),
    )

    assert monitor.send("status") is expected
    assert calls == [
        (
            ("https://api.telegram.org/bottest-token/sendMessage",),
            {
                "data": {"chat_id": "test-chat", "text": "status"},
                "proxies": {"https": "proxy"},
                "timeout": config.MONITOR_TELEGRAM_REQUEST_TIMEOUT_SECONDS,
            },
        )
    ]
    if response_status != 200:
        assert "Monitor notification failed" in caplog.text


def test_send_returns_false_and_logs_request_exception(monkeypatch, caplog):
    monkeypatch.setattr(
        monitor.requests,
        "post",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            OSError("connection failed")
        ),
    )

    assert monitor.send("status") is False
    assert "Telegram send failed: connection failed" in caplog.text


@pytest.mark.parametrize(
    ("age_seconds", "line", "expected_status", "expected_age"),
    [
        (
            config.MONITOR_STALE_LIMIT_SECONDS - 1,
            "Fresh log",
            "✅ OK",
            config.MONITOR_STALE_LIMIT_SECONDS - 1,
        ),
        (
            config.MONITOR_STALE_LIMIT_SECONDS,
            "Stale log",
            "❌ STALE (no activity)",
            config.MONITOR_STALE_LIMIT_SECONDS,
        ),
        (None, "unparseable", "⚠️ NO TIMESTAMP / LOG ERROR", None),
    ],
)
def test_main_reports_fresh_stale_and_invalid_log_timestamps(
    monkeypatch, age_seconds, line, expected_status, expected_age
):
    fixed_now = datetime(2026, 10, 23, 12, 0, 0)

    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed_now

    sent_messages = []

    class StopAfterOneCycle(Exception):
        pass

    if age_seconds is None:
        last_line = line
    else:
        timestamp = fixed_now - timedelta(seconds=age_seconds)
        last_line = f"{timestamp:%Y-%m-%d %H:%M:%S} - INFO - {line}"

    monkeypatch.setattr(monitor, "datetime", FixedDateTime)
    monkeypatch.setattr(monitor, "get_last_line", lambda: last_line)
    monkeypatch.setattr(monitor, "send", sent_messages.append)
    monkeypatch.setattr(
        monitor.time,
        "sleep",
        lambda seconds: (_ for _ in ()).throw(StopAfterOneCycle()),
    )

    with pytest.raises(StopAfterOneCycle):
        monitor.main()

    assert len(sent_messages) == 1
    assert expected_status in sent_messages[0]
    assert line in sent_messages[0]
    if expected_age is None:
        assert "\nAge:" not in sent_messages[0]
    else:
        assert f"\nAge: {expected_age} sec" in sent_messages[0]


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
