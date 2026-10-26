import time
import requests
import os
import sys
from datetime import datetime

# Определяем директорию родительского проекта
PARENT_DIR = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)

sys.path.insert(0, PARENT_DIR)
from app import config
from app.config import (
    TELEGRAM_API_URL as TELEGRAM_API_URL_TEMPLATE,
    TELEGRAM_PROXIES,
)
from app.logger import format_console_log_message, setup_logger

logger = setup_logger(__name__, 'monitor.log')


# Отправляет статус мониторинга в Telegram.
def send(msg):
    url = TELEGRAM_API_URL_TEMPLATE.format(token=config.MONITOR_TELEGRAM_TOKEN)
    try:
        response = requests.post(
            url,
            data={"chat_id": config.MONITOR_CHAT_ID, "text": msg},
            proxies=TELEGRAM_PROXIES,
            timeout=config.MONITOR_TELEGRAM_REQUEST_TIMEOUT_SECONDS,
        )
        if response.status_code != 200:
            logger.error(f"Monitor notification failed: {response.text}")
            return False
        return True
    except Exception as e:
        logger.error(f"Telegram send failed: {e}")
        return False


# Читает последнюю строку лога без загрузки всего файла в память.
def get_last_line():
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


# Извлекает время из строки лога.
def parse_time(line):
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


# Периодически проверяет свежесть лога и отправляет статус.
def main():
    while True:
        last_line = get_last_line()

        status_time = parse_time(last_line)

        now = datetime.now()

        if status_time:
            age = (now - status_time).total_seconds()

            if age < config.MONITOR_STALE_LIMIT_SECONDS:
                status = "✅ OK"
            else:
                status = "❌ STALE (no activity)"
        else:
            age = None
            status = "⚠️ NO TIMESTAMP / LOG ERROR"

        compact_last_line = (
            format_console_log_message(last_line) if last_line else last_line
        )
        msg = f"{status}\nLast log:\n{compact_last_line}"

        if age is not None:
            msg += f"\nAge: {int(age)} sec"

        send(msg)

        time.sleep(config.MONITOR_CHECK_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()