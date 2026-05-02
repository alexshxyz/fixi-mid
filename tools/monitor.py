import time
import requests
import os
from dotenv import load_dotenv
from datetime import datetime

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), '.env'))

LOG_FILE = os.path.join(os.path.dirname(__file__), 'bot.log')
TELEGRAM_TOKEN = os.environ.get('MONITOR_TELEGRAM_TOKEN')
CHAT_ID = os.environ.get('MONITOR_CHAT_ID')

CHECK_INTERVAL = 1800  # 30 минут
STALE_LIMIT = 120      # 2 минуты без логов = проблема


def send(msg):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        requests.post(url, data={"chat_id": CHAT_ID, "text": msg}, timeout=5)
    except Exception as e:
        print(f"Telegram send failed: {e}")


def get_last_line():
    try:
        with open(LOG_FILE, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()
            return lines[-1].strip() if lines else None
    except Exception as e:
        return f"ERROR reading log: {e}"


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