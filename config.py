import json
import os
from datetime import datetime, timezone
from pathlib import Path
from dotenv import load_dotenv

# Настройки проекта
APP_DIR = Path(__file__).resolve().parent
load_dotenv(dotenv_path=APP_DIR / '.env')

# Настройки страницы
SITE_URL = "https://live11.nowgoal26.com/"
BROWSER_HEADLESS = os.environ.get('BROWSER_HEADLESS', 'true').strip().lower() in {'1', 'true', 'yes', 'on'}
TELEGRAM_API_URL = "https://api.telegram.org/bot{token}/sendMessage"

# Настройки файлов
MATCHES_FILE = os.environ.get('MATCHES_FILE', str(APP_DIR / 'matches.json'))
LEAGUES_FILE = str(APP_DIR / 'leagues.json')
STATE_SAVE_FILE = str(APP_DIR / 'match_state.json')

# Настройки Telegram-бота
BOT_TOKEN = os.environ.get('BOT_TOKEN')
CHANNEL_ID = os.environ.get('CHANNEL_ID')

# Настройки прокси
TELEGRAM_PROXY_HOST = os.environ.get('TELEGRAM_PROXY_HOST')
TELEGRAM_PROXY_PORT = os.environ.get('TELEGRAM_PROXY_PORT')
TELEGRAM_PROXY_USERNAME = os.environ.get('TELEGRAM_PROXY_USERNAME')
TELEGRAM_PROXY_PASSWORD = os.environ.get('TELEGRAM_PROXY_PASSWORD')

THRESHOLD = 0.61 # Максимальное значение коэффициента последней строки перед closed (0.61)
MAX_ODD = 0.80 # Минимально допустимый коэффициент для начала отслеживания матча (0.80)

PAGE_RELOAD_MIN_SECONDS = 100 # Перезагрузка через клик по live
PAGE_RELOAD_MAX_SECONDS = 120 # Перезагрузка через клик по live
PAGE_REFRESH_INTERVAL_SECONDS = 3600 # Жесткая перезагрузка страницы

RESTART_HOURS = 24 # Плановая перезагрузка скрипта
NOTIFICATION_CHECK_DELAY_SECONDS = 180 # Задержка проверки статуса Telegram-сигнала


# Преобразуем timestamp cookie в Unix-время.
def _cookie_expires(raw_value):
    return int(
        datetime.strptime(raw_value, "%Y-%m-%dT%H:%M:%S.%fZ")
        .replace(tzinfo=timezone.utc)
        .timestamp()
    )


SITE_COOKIES = [
    {
        "name": "nowgoal26_SelCompany_V2",
        "value": "3",
        "domain": "live11.nowgoal26.com",
        "path": "/",
        "expires": _cookie_expires("2026-10-13T11:54:21.000Z"),
    },
    {
        "name": "orderby",
        "value": "time",
        "domain": "live11.nowgoal26.com",
        "path": "/",
        "expires": _cookie_expires("2026-10-13T11:51:32.000Z"),
    },
    {
        "name": "Default_TimeZone",
        "value": "3",
        "domain": "live11.nowgoal26.com",
        "path": "/",
        "expires": _cookie_expires("2026-10-13T11:51:00.000Z"),
    },
    {
        "name": "isOddsShow",
        "value": "1",
        "domain": "live11.nowgoal26.com",
        "path": "/",
        "expires": _cookie_expires("2026-10-13T11:55:57.000Z"),
    },
    {
        "name": "OddsShowType",
        "value": "9",
        "domain": "live11.nowgoal26.com",
        "path": "/",
        "expires": _cookie_expires("2026-10-13T11:55:57.000Z"),
    },
    {
        "name": "goalWindowCheck",
        "value": "0",
        "domain": "live11.nowgoal26.com",
        "path": "/",
        "expires": _cookie_expires("2026-10-13T11:56:28.000Z"),
    },
    {
        "name": "redWindowCheck",
        "value": "0",
        "domain": "live11.nowgoal26.com",
        "path": "/",
        "expires": _cookie_expires("2026-10-13T11:56:47.000Z"),
    },
    {
        "name": "YellowCheck",
        "value": "0",
        "domain": "live11.nowgoal26.com",
        "path": "/",
        "expires": _cookie_expires("2026-10-13T11:57:02.000Z"),
    },
]

with open(LEAGUES_FILE, 'r', encoding='utf-8') as f:
    LEAGUES_DATA = json.load(f)

LEAGUES_LIST = [item['name'] for item in LEAGUES_DATA['leagues']]
