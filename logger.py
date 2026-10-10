import logging
import os
import re
from copy import copy


class _ConsoleLogHandler(logging.StreamHandler):
    def emit(self, record):
        console_record = copy(record)
        console_record.msg = re.sub(
            r"((?:Active|New|Removed) \d+|Matches (?:updated|found): \d+)"
            r"\s+\(\d+(?:,\s*\d+)*\)",
            r"\1",
            console_record.getMessage(),
        )
        console_record.args = ()
        super().emit(console_record)


# Создаёт именованный логгер с выводом в файл и консоль.
def setup_logger(name, log_filename='bot.log'):
    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)

    log_file = os.path.join(os.path.dirname(__file__), log_filename)
    file_handler = logging.FileHandler(log_file)
    file_handler.setLevel(logging.INFO)
    formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
    file_handler.setFormatter(formatter)

    console_handler = _ConsoleLogHandler()
    console_handler.setLevel(logging.DEBUG)
    console_handler.setFormatter(formatter)

    if not logger.handlers:
        logger.addHandler(file_handler)
        logger.addHandler(console_handler)

    return logger