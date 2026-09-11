import json
import os
from pathlib import Path
from dotenv import load_dotenv

APP_DIR = Path(__file__).resolve().parent
load_dotenv(dotenv_path=APP_DIR / '.env')

site_url = "https://live11.nowgoal26.com/"
telegram_api_url = "https://api.telegram.org/bot{token}/sendMessage"

matches_file = os.environ.get('MATCHES_FILE', str(APP_DIR / 'matches.json'))
leagues_file = str(APP_DIR / 'leagues.json')
state_save_file = str(APP_DIR / 'match_state.json')

bot_token = os.environ.get('BOT_TOKEN')
channel_id = os.environ.get('CHANNEL_ID')

telegram_proxy_host = os.environ.get('TELEGRAM_PROXY_HOST')
telegram_proxy_port = os.environ.get('TELEGRAM_PROXY_PORT')
telegram_proxy_username = os.environ.get('TELEGRAM_PROXY_USERNAME')
telegram_proxy_password = os.environ.get('TELEGRAM_PROXY_PASSWORD')

threshold = 0.61 # Максимальное значение коэффициента последней строки перед closed 
max_odd = 0.80 # Минимально допустимый коэффициент для начала отслеживания матча
restart_hours = 8 # Плановая перезагрузка скрипта

# Перезагрузка страницы в секундах (случайное значение между min и max)
page_reload_min_seconds = 420 
page_reload_max_seconds = 480

with open(leagues_file, 'r', encoding='utf-8') as f:
    leagues_data = json.load(f)

leagues_list = [item['name'] for item in leagues_data['leagues']]
