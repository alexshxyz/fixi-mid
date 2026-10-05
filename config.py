import json
import os
import random
from pathlib import Path
from dotenv import load_dotenv

# Настройки проекта
APP_DIR = Path(__file__).resolve().parent
load_dotenv(dotenv_path=APP_DIR / '.env')

# Настройки страницы
SITE_URL = "https://www.goaloo.com/"
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

THRESHOLD = 1.61 # Максимальный коэффициент перед Closed (1.61)
MAX_ODD = 1.80 # Минимальный коэффициент для подтверждения паттерна (1.80)
OVER_TOTAL_DROP_THRESHOLD = 25 # Минимальное снижение тотала для новой Over-стратегии (0.50)
OVER_TOTAL_DROP_MAX = 75 # Максимальное снижение тотала (не включая границу) (0.75)
SKIPMATCH = ["FT", "ET"] # Время матча, при котором новая Over-стратегия не анализирует пару записей
DEBUGMODE = 0 # Запись историй анализа в data.json и data2.json и count.json: 0 — выключена, 1 — включена

TABLE_LIVE_RELOAD = random.randint(100, 120) # Перезагрузка таблицы Live
PAGE_LIVE_RELOAD = 1800 # Полная перезагрузка страницы

RESTART_HOURS = 24 # Плановая перезагрузка скрипта
NOTIFICATION_CHECK_DELAY_SECONDS = 180 # Задержка проверки статуса Telegram-сигнала

# Куки сайта
SITE_COOKIE_DOMAIN = "www.goaloo.com"
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
