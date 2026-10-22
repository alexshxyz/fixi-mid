import time
import requests
import os
import sys
from urllib.parse import quote
from dotenv import load_dotenv
from datetime import datetime

# Определяем директорию родительского проекта
PARENT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

sys.path.insert(0, PARENT_DIR)
from config import (
    BOT_TOKEN,
    CHANNEL_ID,
    TELEGRAM_API_URL as TELEGRAM_API_URL_TEMPLATE,
    TELEGRAM_PROXY_HOST,
    TELEGRAM_PROXY_PASSWORD,
    TELEGRAM_PROXY_PORT,
    TELEGRAM_PROXY_USERNAME,
)
from logger import format_console_log_message, setup_logger

load_dotenv(dotenv_path=os.path.join(PARENT_DIR, '.env'))

LOG_FILE = os.path.join(PARENT_DIR, 'bot.log')
TELEGRAM_TOKEN = os.environ.get('MONITOR_TELEGRAM_TOKEN') or BOT_TOKEN
CHAT_ID = os.environ.get('MONITOR_CHAT_ID') or CHANNEL_ID

TELEGRAM_PROXIES = None
if TELEGRAM_PROXY_HOST and TELEGRAM_PROXY_PORT:
    proxy_auth = ''
    if TELEGRAM_PROXY_USERNAME and TELEGRAM_PROXY_PASSWORD:
        proxy_auth = (
            f'{quote(TELEGRAM_PROXY_USERNAME, safe="")}:'
            f'{quote(TELEGRAM_PROXY_PASSWORD, safe="")}@'
        )

    telegram_proxy_url = (
        f'socks5h://{proxy_auth}{TELEGRAM_PROXY_HOST}:{TELEGRAM_PROXY_PORT}'
    )
    TELEGRAM_PROXIES = {
        'http': telegram_proxy_url,
        'https': telegram_proxy_url,
    }

logger = setup_logger(__name__, 'monitor.log')

CHECK_INTERVAL = 1800  # 30 минут
STALE_LIMIT = 120      # 2 минуты без логов = проблема
LOG_READ_CHUNK_SIZE = 4096  # Размер блока при поиске последней строки лога


# Отправляет статус мониторинга в Telegram.
def send(msg):
    url = TELEGRAM_API_URL_TEMPLATE.format(token=TELEGRAM_TOKEN)
    try:
        response = requests.post(
            url,
            data={"chat_id": CHAT_ID, "text": msg},
            proxies=TELEGRAM_PROXIES,
            timeout=5,
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
        with open(LOG_FILE, "rb") as log_file:
            log_file.seek(0, os.SEEK_END)
            file_end = log_file.tell()
            if file_end == 0:
                return None

            log_file.seek(file_end - 1)
            offset = file_end - 1 if log_file.read(1) == b"\n" else file_end

            while offset > 0:
                chunk_start = max(0, offset - LOG_READ_CHUNK_SIZE)
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

            if age < STALE_LIMIT:
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

        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    main()