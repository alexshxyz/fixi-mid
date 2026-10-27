import json
import os
import random
from pathlib import Path
from urllib.parse import quote
from dotenv import load_dotenv

# Настройки проекта
APP_DIR: Path = Path(__file__).resolve().parent
PROJECT_DIR: Path = APP_DIR.parent
DATA_DIR: Path = PROJECT_DIR / 'data'
load_dotenv(dotenv_path=PROJECT_DIR / '.env')

# Настройки страницы
SITE_URL = "https://www.goaloo.com/"
BROWSER_HEADLESS: bool = os.environ.get('BROWSER_HEADLESS', 'true').strip().lower() in {'1', 'true', 'yes', 'on'}
BROWSER_PROXY_SERVER: str | None = os.environ.get('BROWSER_PROXY_SERVER')
TELEGRAM_API_URL = "https://api.telegram.org/bot{token}/sendMessage"


def validate_browser_proxy_server(proxy_server: str | None) -> None:
    """Проверить, что задан обязательный адрес прокси браузера."""
    if not proxy_server or not proxy_server.strip():
        raise RuntimeError("BROWSER_PROXY_SERVER is not configured")

# Настройки файлов
MATCHES_FILE: str = os.environ.get('MATCHES_FILE', str(DATA_DIR / 'matches.json'))
LEAGUES_FILE: str = str(DATA_DIR / 'leagues.json')
STATE_SAVE_FILE: str = str(DATA_DIR / 'match_state.json')

# Настройки Telegram-бота
BOT_TOKEN: str | None = os.environ.get('BOT_TOKEN')
CHANNEL_ID: str | None = os.environ.get('CHANNEL_ID')
MONITOR_TELEGRAM_TOKEN: str | None = os.environ.get('MONITOR_TELEGRAM_TOKEN') or BOT_TOKEN
MONITOR_CHAT_ID: str | None = os.environ.get('MONITOR_CHAT_ID') or CHANNEL_ID

# Настройки прокси
TELEGRAM_PROXY_HOST: str | None = os.environ.get('TELEGRAM_PROXY_HOST')
TELEGRAM_PROXY_PORT: str | None = os.environ.get('TELEGRAM_PROXY_PORT')
TELEGRAM_PROXY_USERNAME: str | None = os.environ.get('TELEGRAM_PROXY_USERNAME')
TELEGRAM_PROXY_PASSWORD: str | None = os.environ.get('TELEGRAM_PROXY_PASSWORD')
TELEGRAM_PROXIES: dict[str, str] | None = None
if TELEGRAM_PROXY_HOST and TELEGRAM_PROXY_PORT:
    proxy_auth: str = ''
    if TELEGRAM_PROXY_USERNAME and TELEGRAM_PROXY_PASSWORD:
        proxy_auth = (
            f'{quote(TELEGRAM_PROXY_USERNAME, safe="")}:'
            f'{quote(TELEGRAM_PROXY_PASSWORD, safe="")}@'
        )

    telegram_proxy_url: str = (
        f'socks5h://{proxy_auth}{TELEGRAM_PROXY_HOST}:{TELEGRAM_PROXY_PORT}'
    )
    TELEGRAM_PROXIES = {
        'http': telegram_proxy_url,
        'https': telegram_proxy_url,
    }

THRESHOLD = 1.61 # Максимальный коэффициент перед Closed (1.61)
START_ODD = 1.80 # Минимальный коэффициент для подтверждения паттерна (1.80)

DEBUGMODE = 0 # Запись истории анализа в data_closed_ov_ah.json и count.json: 0 — выключена, 1 — включена

TABLE_LIVE_RELOAD_MIN_SECONDS = 100
TABLE_LIVE_RELOAD_MAX_SECONDS = 120


def get_table_live_reload_interval() -> int:
    """Случайно выбрать интервал обновления Live-таблицы."""
    return random.randint(
        TABLE_LIVE_RELOAD_MIN_SECONDS,
        TABLE_LIVE_RELOAD_MAX_SECONDS,
    )


PAGE_LIVE_RELOAD = 3600 # Полная перезагрузка страницы

RESTART_HOURS = 24 # Плановая перезагрузка скрипта
NOTIFICATION_CHECK_DELAY_SECONDS = 180 # Задержка проверки статуса Telegram-сигнала

# Таймауты и интервалы ожидания страницы.
PAGE_LOAD_TIMEOUT_MS = 30_000
PAGE_ELEMENT_WAIT_TIMEOUT_MS = 10_000
PAGE_REFRESH_TABLE_TIMEOUT_MS = 20_000
PAGE_DATA_READY_TIMEOUT_SECONDS = 60
PAGE_POLL_INTERVAL_MS = 1_000
LIVE_CHANGE_POLL_INTERVAL_MS = 100

# Повторные попытки и периодические проверки.
PAGE_LOAD_MAX_RETRIES = 2
PAGE_PREPARE_MAX_RETRIES = 2
PAGE_ACTION_MAX_RETRIES = 3
PAGE_ACTION_RETRY_DELAY_SECONDS = 10
LIVE_REFRESH_MAX_RETRIES = 2
LIVE_REFRESH_RETRY_DELAY_SECONDS = 5
HARD_PAGE_REFRESH_MAX_RETRIES = 2
HARD_PAGE_REFRESH_RETRY_DELAY_SECONDS = 10
NO_MATCH_RETRY_DELAY_SECONDS = 60
HEARTBEAT_INTERVAL_SECONDS = 100
PAGE_REFRESH_FAILURE_RETRY_DELAY_SECONDS = 10

# Настройки мониторинга логов.
BOT_LOG_FILE: Path = PROJECT_DIR / 'bot.log'
ERROR_SCREENSHOTS_DIR: Path = PROJECT_DIR / 'logs' / 'screenshots'
MONITOR_CHECK_INTERVAL_SECONDS = 1800
MONITOR_STALE_LIMIT_SECONDS = 120
MONITOR_LOG_READ_CHUNK_SIZE = 4096
MONITOR_TELEGRAM_REQUEST_TIMEOUT_SECONDS = 5

# Параметры запросов и обновления сообщений Telegram.
TELEGRAM_REQUEST_TIMEOUT_SECONDS = 10
TELEGRAM_RETRY_DELAY_SECONDS = 5
TELEGRAM_SEND_MAX_ATTEMPTS = 3
TELEGRAM_EDIT_MAX_ATTEMPTS = 3
TELEGRAM_EDIT_RETRY_DELAY_SECONDS = 10

# Куки сайта
SITE_COOKIE_DOMAIN = "www.goaloo.com"
FINISHED_TIME_MARKERS = ("90", "90+", "FT", "ET", "Penalty")
SITE_COOKIES = [
    {"name": name, "value": value, "domain": SITE_COOKIE_DOMAIN, "path": "/"}
    for name, value in [
        ("goaloo_SelCompany_V2", "3"),
        ("orderby", "time"),
        ("Default_TimeZone", "3"),
        ("isOddsShow", "1"),
        ("OddsShowType", "9"),
        ("goalWindowCheck", "0"),
        ("redWindowCheck", "0"),
        ("YellowCheck", "0"),
    ]
]

with open(LEAGUES_FILE, 'r', encoding='utf-8') as f:
    LEAGUES_LIST = json.load(f)
