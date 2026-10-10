import logging

import logger as logger_module


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

        assert "DEBUG - debug message" in capsys.readouterr().err
        contents = log_file.read_text(encoding="utf-8")
        assert "INFO - info message" in contents
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
            "Matches synchronized: Active 2 New 0 (-) Removed 1"
            in console_output
        )
        assert "Matches updated: 2" in console_output
        assert "Matches found: 1" in console_output
        assert "3003900" not in console_output
        assert "3003901" not in console_output
        assert "3087993" not in console_output

        file_output = log_file.read_text(encoding="utf-8")
        assert "Active 2 (3003900, 3003901)" in file_output
        assert "Removed 1 (3087993)" in file_output
        assert "Matches updated: 2 (3003900, 3003901)" in file_output
        assert "Matches found: 1 (3087993)" in file_output
    finally:
        for handler in logger.handlers[:]:
            logger.removeHandler(handler)
            handler.close()
