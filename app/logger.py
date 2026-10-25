import logging
import re
from copy import copy
from pathlib import Path


_MATCH_IDS_PATTERN = re.compile(
    r"((?:Active|New|Removed) \d+|Matches (?:updated|found): \d+)"
    r"\s*\([^)]*\)"
)


def format_console_log_message(message):
    return _MATCH_IDS_PATTERN.sub(r"\1", message)


class _ConsoleLogHandler(logging.StreamHandler):
    def emit(self, record):
        console_record = copy(record)
        console_record.msg = format_console_log_message(record.getMessage())
        console_record.args = ()
        super().emit(console_record)


# Создаёт именованный логгер с выводом в файл и консоль.
def setup_logger(name, log_filename='bot.log'):
    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)

    project_dir = Path(__file__).resolve().parent.parent
    log_file = project_dir / log_filename
    file_handler = logging.FileHandler(log_file)
    file_handler.setLevel(logging.INFO)
    formatter = logging.Formatter(
        '%(asctime)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
    )
    file_handler.setFormatter(formatter)

    console_handler = _ConsoleLogHandler()
    console_handler.setLevel(logging.DEBUG)
    console_handler.setFormatter(formatter)

    if not logger.handlers:
        logger.addHandler(file_handler)
        logger.addHandler(console_handler)

    return logger