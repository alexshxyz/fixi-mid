import html
import time
import requests
import os
import sys
from datetime import datetime
from typing import NoReturn

# Определяем директорию родительского проекта
PARENT_DIR = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)

sys.path.insert(0, PARENT_DIR)
from app import config
from app.config import Settings
from app.logger import format_console_log_message, setup_logger

logger = setup_logger(__name__, 'monitor.log')


def send_monitor_notification(
    msg: str,
    settings: Settings | None = None,
) -> bool:
    """Отправить сообщение о состоянии мониторинга в Telegram."""
    runtime_settings = settings if settings is not None else config.load_settings()
    url = runtime_settings.monitor_telegram_api_url
    try:
        response = requests.post(
            url,
            data={
                "chat_id": runtime_settings.monitor_chat_id,
                "text": msg,
                "parse_mode": "HTML",
            },
            proxies=runtime_settings.telegram_proxies,
            timeout=config.MONITOR_TELEGRAM_REQUEST_TIMEOUT_SECONDS,
        )
        if response.status_code != 200:
            logger.error(f"Monitor notification failed: {response.text}")
            return False
        return True
    except Exception as e:
        logger.error(f"Telegram send failed: {e}")
        return False


def get_last_log_line() -> str | None:
    """Прочитать последнюю строку файла лога, не загружая его целиком."""
    try:
        with open(config.BOT_LOG_FILE, "rb") as log_file:
            log_file.seek(0, os.SEEK_END)
            file_end = log_file.tell()
            if file_end == 0:
                return None

            log_file.seek(file_end - 1)
            offset = file_end - 1 if log_file.read(1) == b"\n" else file_end

            while offset > 0:
                chunk_start = max(0, offset - config.MONITOR_LOG_READ_CHUNK_SIZE)
                log_file.seek(chunk_start)
                chunk = log_file.read(offset - chunk_start)
                newline_index = chunk.rfind(b"\n")
                if newline_index >= 0:
                    offset = chunk_start + newline_index + 1
                    break
                offset = chunk_start

            log_file.seek(offset)
            return log_file.read(file_end - offset).decode(
                "utf-8", errors="ignore"
            ).strip()
    except Exception as e:
        logger.error(f"Error reading bot log: {e}")
        return None


def parse_log_time(line: str | None) -> datetime | None:
    """Извлечь дату и время из строки лога."""
    try:
        if not line:
            return None

        ts = line.split(" - ")[0].strip()
        for timestamp_format in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S,%f"):
            try:
                return datetime.strptime(ts, timestamp_format)
            except ValueError:
                continue
    except (AttributeError, IndexError):
        return None
    return None


def main() -> NoReturn:
    """Периодически проверять свежесть лога и отправлять статус."""
    settings = config.load_settings()
    while True:
        last_line = get_last_log_line()

        status_time = parse_log_time(last_line)

        now = datetime.now()
        status_icon = "🟨"

        if status_time:
            age = (now - status_time).total_seconds()

            if age < config.MONITOR_STALE_LIMIT_SECONDS:
                status_icon = "🟩"
            else:
                status_icon = "🟥"
        else:
            age = None

        compact_last_line = (
            format_console_log_message(last_line) if last_line else last_line
        )
        msg = (
            f"<b>{status_icon} Last log:</b>\n"
            f"<code>{html.escape(str(compact_last_line))}</code>"
        )

        if age is not None:
            msg += f"\n<code>Age: {int(age)} sec</code>"

        send_monitor_notification(msg, settings)

        time.sleep(config.MONITOR_CHECK_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()