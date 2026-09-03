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
from logging_config import setup_logger

load_dotenv(dotenv_path=os.path.join(PARENT_DIR, '.env'))

LOG_FILE = os.path.join(PARENT_DIR, 'bot.log')
TELEGRAM_TOKEN = os.environ.get('MONITOR_TELEGRAM_TOKEN') or os.environ.get('BOT_TOKEN')
CHAT_ID = os.environ.get('MONITOR_CHAT_ID') or os.environ.get('CHANNEL_ID')
TELEGRAM_PROXY_HOST = os.environ.get('TELEGRAM_PROXY_HOST')
TELEGRAM_PROXY_PORT = os.environ.get('TELEGRAM_PROXY_PORT')
TELEGRAM_PROXY_USERNAME = os.environ.get('TELEGRAM_PROXY_USERNAME')
TELEGRAM_PROXY_PASSWORD = os.environ.get('TELEGRAM_PROXY_PASSWORD')

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


def send(msg):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
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


def get_last_line():
    try:
        with open(LOG_FILE, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()
            return lines[-1].strip() if lines else None
    except Exception as e:
        logger.error(f"Error reading bot log: {e}")
        return None


def parse_time(line):
    """
    Ожидаемый формат лога:
    2026-04-16 18:30:10,123 - INFO - Match 123 updated
    """
    try:
        if not line:
            return None

        ts = line.split(" - ")[0].strip()
        return datetime.strptime(ts, "%Y-%m-%d %H:%M:%S,%f")

    except Exception:
        return None


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

        msg = f"{status}\nLast log:\n{last_line}"

        if age is not None:
            msg += f"\nAge: {int(age)} sec"

        send(msg)

        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    main()