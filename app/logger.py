import logging
import os
import re
from copy import copy
from pathlib import Path


_MATCH_IDS_PATTERN = re.compile(
    r"((?:Active|New|Removed) \d+|Matches (?:updated|found): \d+)"
    r"\s*\([^)]*\)"
)
_SENSITIVE_ENVIRONMENT_VARIABLES = (
    'BOT_TOKEN',
    'MONITOR_TELEGRAM_TOKEN',
    'CHANNEL_ID',
    'MONITOR_CHAT_ID',
)


class _SensitiveDataFilter(logging.Filter):
    def filter(self, record):
        secrets = sorted(
            {
                value
                for name in _SENSITIVE_ENVIRONMENT_VARIABLES
                if (value := os.environ.get(name))
            },
            key=len,
            reverse=True,
        )
        if not secrets:
            return True

        message = record.getMessage()
        for secret in secrets:
            message = message.replace(secret, '[REDACTED]')
        record.msg = message
        record.args = ()

        if record.exc_info:
            formatter = logging.Formatter()
            exception_text = formatter.formatException(record.exc_info)
            for secret in secrets:
                exception_text = exception_text.replace(secret, '[REDACTED]')
            record.exc_text = exception_text
            record.exc_info = None
        elif record.exc_text:
            for secret in secrets:
                record.exc_text = record.exc_text.replace(secret, '[REDACTED]')

        return True


# Убирает подробные ID матчей из сообщений для консольного вывода.
def format_console_log_message(message):
    return _MATCH_IDS_PATTERN.sub(r"\1", message)


class _ConsoleLogHandler(logging.StreamHandler):
    # Форматирует сообщение для консоли, не меняя запись в файловом логе.
    def emit(self, record):
        console_record = copy(record)
        console_record.msg = format_console_log_message(record.getMessage())
        console_record.args = ()
        super().emit(console_record)


# Создаёт именованный логгер с выводом в файл и консоль.
def setup_logger(name, log_filename='bot.log'):
    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)
    if not any(isinstance(item, _SensitiveDataFilter) for item in logger.filters):
        logger.addFilter(_SensitiveDataFilter())

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