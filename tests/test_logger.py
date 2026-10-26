import logging
import re

from app import logger as logger_module


TIMESTAMP_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} - ")


def test_setup_logger_writes_info_to_file_and_debug_to_console(tmp_path, capsys):
    name = f"{__name__}.test_setup_logger"
    log_file = tmp_path / "bot.log"
    logger = logging.getLogger(name)

    try:
        result = logger_module.setup_logger(name, str(log_file))

        assert result is logger
        assert logger.level == logging.DEBUG
        assert len(logger.handlers) == 2

        file_handler = next(
            handler for handler in logger.handlers if isinstance(handler, logging.FileHandler)
        )
        console_handler = next(
            handler
            for handler in logger.handlers
            if isinstance(handler, logging.StreamHandler)
            and not isinstance(handler, logging.FileHandler)
        )
        assert file_handler.level == logging.INFO
        assert console_handler.level == logging.DEBUG

        logger.debug("debug message")
        logger.info("info message")

        console_output = capsys.readouterr().err
        console_lines = console_output.splitlines()
        assert any(
            TIMESTAMP_PATTERN.match(line) and " - DEBUG - debug message" in line
            for line in console_lines
        )
        assert all(TIMESTAMP_PATTERN.match(line) for line in console_lines)
        contents = log_file.read_text(encoding="utf-8")
        file_lines = contents.splitlines()
        assert any(
            TIMESTAMP_PATTERN.match(line) and " - INFO - info message" in line
            for line in file_lines
        )
        assert all(TIMESTAMP_PATTERN.match(line) for line in file_lines)
        assert "debug message" not in contents
    finally:
        for handler in logger.handlers[:]:
            logger.removeHandler(handler)
            handler.close()


def test_match_id_lists_are_hidden_only_in_console(tmp_path, capsys):
    name = f"{__name__}.test_match_id_lists"
    log_file = tmp_path / "bot.log"
    logger = logger_module.setup_logger(name, str(log_file))

    try:
        logger.info(
            "Matches synchronized: Active %d (%s) New %d (%s) "
            "Removed %d (%s)",
            2,
            "3003900, 3003901",
            0,
            "-",
            1,
            "3087993",
        )
        logger.info("Matches updated: %d (%s)", 2, "3003900, 3003901")
        logger.info("Matches found: %d (%s)", 1, "3087993")

        console_output = capsys.readouterr().err
        assert (
            "Matches synchronized: Active 2 New 0 Removed 1"
            in console_output
        )
        assert "Matches updated: 2" in console_output
        assert "Matches found: 1" in console_output
        assert "3003900" not in console_output
        assert "3003901" not in console_output
        assert "3087993" not in console_output
        assert "(-)" not in console_output
        assert "()" not in console_output

        file_output = log_file.read_text(encoding="utf-8")
        assert "Active 2 (3003900, 3003901)" in file_output
        assert "Removed 1 (3087993)" in file_output
        assert "Matches updated: 2 (3003900, 3003901)" in file_output
        assert "Matches found: 1 (3087993)" in file_output

        logger.info("Matches found: %d (%s)", 0, "")
        assert "Matches found: 0" in capsys.readouterr().err
    finally:
        for handler in logger.handlers[:]:
            logger.removeHandler(handler)
            handler.close()


def test_sensitive_telegram_values_are_redacted_from_logs_and_tracebacks(
    tmp_path, capsys, monkeypatch
):
    token = "123456:secret-token"
    channel_id = "-100123456789"
    monitor_token = "654321:monitor-secret"
    monitor_channel_id = "-100987654321"
    monkeypatch.setenv("BOT_TOKEN", token)
    monkeypatch.setenv("CHANNEL_ID", channel_id)
    monkeypatch.setenv("MONITOR_TELEGRAM_TOKEN", monitor_token)
    monkeypatch.setenv("MONITOR_CHAT_ID", monitor_channel_id)

    name = f"{__name__}.test_sensitive_values"
    log_file = tmp_path / "bot.log"
    logger = logger_module.setup_logger(name, str(log_file))

    try:
        try:
            raise RuntimeError(
                f"Request failed for https://api.telegram.org/bot{token}/sendMessage "
                f"and https://api.telegram.org/bot{monitor_token}/sendMessage"
            )
        except RuntimeError:
            logger.exception(
                "Telegram request to channels %s and %s failed: %s %s",
                channel_id,
                monitor_channel_id,
                token,
                monitor_token,
            )

        console_output = capsys.readouterr().err
        file_output = log_file.read_text(encoding="utf-8")
        for output in (console_output, file_output):
            assert token not in output
            assert channel_id not in output
            assert monitor_token not in output
            assert monitor_channel_id not in output
            assert output.count("[REDACTED]") >= 6
    finally:
        for handler in logger.handlers[:]:
            logger.removeHandler(handler)
            handler.close()
