import json
import os
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, TypedDict, cast
from urllib.parse import quote

from dotenv import load_dotenv

# Настройки проекта
APP_DIR: Path = Path(__file__).resolve().parent
PROJECT_DIR: Path = APP_DIR.parent
DATA_DIR: Path = PROJECT_DIR / "data"

# Настройки страницы
SITE_URL: str = "https://www.goaloo.com/"
BROWSER_HEADLESS: bool = True
TELEGRAM_API_URL: str = "https://api.telegram.org/bot{token}/sendMessage"

# Настройки файлов
MATCHES_FILE: str = str(DATA_DIR / "matches.json")
LEAGUES_FILE: str = str(DATA_DIR / "leagues.json")
STATE_SAVE_FILE: str = str(DATA_DIR / "match_state.json")

# Пороги коэффициентов
THRESHOLD: float = 1.61
START_ODD: float = 1.80

# Диагностические записи
DEBUGMODE: bool = False

# Включение/отключение новой стратегии
WITHOUT_CLOSED_STRATEGY_ACTIVE: bool = True

# Интервал обновления Live-таблицы
TABLE_LIVE_RELOAD_MIN_SECONDS: int = 100
TABLE_LIVE_RELOAD_MAX_SECONDS: int = 120


def get_table_live_reload_interval() -> int:
    """Случайно выбрать интервал обновления Live-таблицы."""
    return random.randint(
        TABLE_LIVE_RELOAD_MIN_SECONDS,
        TABLE_LIVE_RELOAD_MAX_SECONDS,
    )


# Полная перезагрузка страницы
PAGE_LIVE_RELOAD: int = 3600

# Плановая перезагрузка скрипта
RESTART_HOURS: int = 24

# Задержка проверки статуса Telegram-сигнала
NOTIFICATION_CHECK_DELAY_SECONDS: int = 180

# Таймауты и интервалы ожидания страницы.
PAGE_DOM_LOAD_TIMEOUT_MS: int = 30_000
PAGE_ELEMENT_WAIT_TIMEOUT_MS: int = 10_000
HARDCODE_DELAY_MS: int = 1_000
LIVE_CHANGE_POLL_INTERVAL_MS: int = 100

# Количество попыток и периодические проверки.
PAGE_DOM_LOAD_MAX_ATTEMPTS: int = 2
PREPARE_LIVE_TABLE_MAX_ATTEMPTS: int = 2
FIRST_PAGE_LOAD_RETRY_DELAY_SECONDS: int = 10
LIVE_TABLE_REFRESH_MAX_ATTEMPTS: int = 2
LIVE_TABLE_REFRESH_RETRY_DELAY_SECONDS: int = 5
PAGE_REFRESH_MAX_ATTEMPTS: int = 2
PAGE_REFRESH_RETRY_DELAY_SECONDS: int = 10
NO_MATCHES_RETRY_DELAY_SECONDS: int = 60
HEARTBEAT_INTERVAL_SECONDS: int = 100

# Настройки мониторинга логов.
BOT_LOG_FILE: Path = PROJECT_DIR / "bot.log"
ERROR_SCREENSHOTS_DIR: Path = PROJECT_DIR / "logs" / "screenshots"
MONITOR_CHECK_INTERVAL_SECONDS: int = 1800
MONITOR_STALE_LIMIT_SECONDS: int = 120
MONITOR_LOG_READ_CHUNK_SIZE: int = 4096
MONITOR_TELEGRAM_REQUEST_TIMEOUT_SECONDS: int = 5

# Параметры запросов и обновления сообщений Telegram.
TELEGRAM_REQUEST_TIMEOUT_SECONDS: int = 10
TELEGRAM_RETRY_DELAY_SECONDS: int = 5
TELEGRAM_SEND_MAX_ATTEMPTS: int = 3
TELEGRAM_EDIT_MAX_ATTEMPTS: int = 3
TELEGRAM_EDIT_RETRY_DELAY_SECONDS: int = 10

# Маркеры завершенных матчей
FINISHED_TIME_MARKERS: tuple[str, ...] = ("90", "90+", "FT", "ET", "Penalty")


class SiteCookie(TypedDict, total=False):
    name: str
    value: str
    url: str | None
    domain: str | None
    path: str | None
    expires: float | None
    httpOnly: bool | None
    secure: bool | None
    sameSite: Literal["Lax", "None", "Strict"] | None
    partitionKey: str | None


# Куки сайта
SITE_COOKIE_DOMAIN: str = "www.goaloo.com"
SITE_COOKIES: list[SiteCookie] = [
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


@dataclass(frozen=True)
class Settings:
    browser_proxy_server: str
    bot_token: str
    channel_id: str
    monitor_telegram_token: str
    monitor_chat_id: str
    telegram_proxy_host: str
    telegram_proxy_port: str
    telegram_proxies: dict[str, str] | None

    @property
    def telegram_api_url(self) -> str:
        return TELEGRAM_API_URL.format(token=self.bot_token)

    @property
    def telegram_edit_message_url(self) -> str:
        return self.telegram_api_url.rsplit("/", 1)[0] + "/editMessageText"

    @property
    def monitor_telegram_api_url(self) -> str:
        return TELEGRAM_API_URL.format(token=self.monitor_telegram_token)


def load_settings() -> Settings:
    """Загрузить настройки окружения и .env при явном вызове."""
    load_dotenv(dotenv_path=PROJECT_DIR / ".env")

    bot_token = os.environ.get("BOT_TOKEN", "")
    channel_id = os.environ.get("CHANNEL_ID", "")
    telegram_proxy_host = os.environ.get("TELEGRAM_PROXY_HOST", "")
    telegram_proxy_port = os.environ.get("TELEGRAM_PROXY_PORT", "")
    proxy_username = os.environ.get("TELEGRAM_PROXY_USERNAME")
    proxy_password = os.environ.get("TELEGRAM_PROXY_PASSWORD")

    telegram_proxies: dict[str, str] | None = None
    if telegram_proxy_host and telegram_proxy_port:
        proxy_auth = ""
        if proxy_username and proxy_password:
            proxy_auth = (
                f'{quote(proxy_username, safe="")}:'
                f'{quote(proxy_password, safe="")}@'
            )
        proxy_url = (
            f"socks5h://{proxy_auth}{telegram_proxy_host}:{telegram_proxy_port}"
        )
        telegram_proxies = {
            "http": proxy_url,
            "https": proxy_url,
        }

    return Settings(
        browser_proxy_server=os.environ.get("BROWSER_PROXY_SERVER", ""),
        bot_token=bot_token,
        channel_id=channel_id,
        monitor_telegram_token=(
            os.environ.get("MONITOR_TELEGRAM_TOKEN") or bot_token
        ),
        monitor_chat_id=os.environ.get("MONITOR_CHAT_ID") or channel_id,
        telegram_proxy_host=telegram_proxy_host,
        telegram_proxy_port=telegram_proxy_port,
        telegram_proxies=telegram_proxies,
    )


def load_leagues(path: str | Path = LEAGUES_FILE) -> list[str]:
    """Загрузить список лиг из JSON-файла при явном вызове."""
    with open(path, "r", encoding="utf-8") as leagues_file:
        leagues: object = json.load(leagues_file)
    if not isinstance(leagues, list):
        raise ValueError(f"Expected a list of league names in {path}")
    raw_leagues = cast(list[object], leagues)
    result: list[str] = []
    for league in raw_leagues:
        if not isinstance(league, str):
            raise ValueError(f"Expected a list of league names in {path}")
        result.append(league)
    return result
