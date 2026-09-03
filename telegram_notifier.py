import requests
import os
import json
from urllib.parse import quote
from dotenv import load_dotenv

from storage import save_match, check_duplicate_match
from logging_config import setup_logger

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), '.env'))

# Загрузка списка лиг
leagues_file = os.path.join(os.path.dirname(__file__), 'leagues.json')
with open(leagues_file, 'r', encoding='utf-8') as f:
    leagues_data = json.load(f)
leagues_list = [item['name'] for item in leagues_data['leagues']]

logger = setup_logger(__name__)

# Настройки для Telegram
BOT_TOKEN = os.environ.get('BOT_TOKEN')
CHANNEL_ID = os.environ.get('CHANNEL_ID')

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

TELEGRAM_API_URL = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"


def _prepare_odds(over_odds):
    try:
        return round(float(over_odds) + 1, 2)
    except (ValueError, TypeError):
        return over_odds


def _build_prediction(over, handicap_text, handicap_team_order):
    if handicap_text is None:
        return f"Over {over} FT"
    return f"Handicap {handicap_text} {handicap_team_order} FT"


def _build_message(league, team1, team2, score, match_url, prediction, odds_value):
    emoji = "🔥" if league in leagues_list else "🔒"
    extra_line = f"<b>{prediction}</b>\n"

    return (
        f"<b>{emoji} Crown</b>\n"
        f"{league}\n"
        f"<b><a href=\"{match_url}\">{team1} {score} {team2}</a></b>\n"
        f"{extra_line}"
        f"Odds {odds_value}"
    )


def _is_duplicate_notification(match_url, prediction, match_id):
    if check_duplicate_match(match_url, prediction):
        logger.info(
            f"[DUPLICATE] Match {match_id} with prediction '{prediction}' already sent. Skipping."
        )
        return True
    return False


def _send_message(payload, match_id=None, success_message=None):
    try:
        response = requests.post(
            TELEGRAM_API_URL,
            json=payload,
            proxies=TELEGRAM_PROXIES,
            timeout=10,
        )
        if response.status_code == 200:
            if success_message:
                logger.info(success_message)
            else:
                logger.info(f"Telegram notification sent for match {match_id}")
            return True

        logger.error(f"Failed to send Telegram notification: {response.text}")
    except Exception as e:
        logger.error(f"Error sending Telegram notification: {e}")
    return False


def send_telegram_message(text):
    """Отправляет произвольное HTML-сообщение в Telegram-канал."""
    payload = {
        "chat_id": CHANNEL_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    return _send_message(payload, success_message="Telegram message sent")


def _save_notification(league, team1, team2, prediction, odds_value, match_url):
    try:
        save_match(
            league=league,
            home_team=team1,
            away_team=team2,
            prediction=prediction,
            odds=odds_value,
            link=match_url,
        )
    except Exception as db_error:
        logger.error(f"Failed to save match to DB: {db_error}")


def send_telegram_notification(league, team1, team2, score, over=None, over_odds=None, match_id=None, handicap_text=None, handicap_team_order=None):
    """Отправляет уведомление о матче в Telegram канал."""
    match_url = f"https://live5.nowgoal26.com/oddscomp/{match_id}" if match_id else ""
    odds_value = _prepare_odds(over_odds)
    prediction = _build_prediction(over, handicap_text, handicap_team_order)
    message = _build_message(
        league,
        team1,
        team2,
        score,
        match_url,
        prediction,
        odds_value,
    )

    payload = {
        "chat_id": CHANNEL_ID,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }

    if _is_duplicate_notification(match_url, prediction, match_id):
        return False

    if not _send_message(payload, match_id):
        return False

    _save_notification(league, team1, team2, prediction, odds_value, match_url)
    return True



if __name__ == "__main__":
    # Для тестирования
    if not BOT_TOKEN or not CHANNEL_ID:
        logger.warning("Please configure BOT_TOKEN and CHANNEL_ID in .env file")
    else:
        send_telegram_notification(
            league="Test League",
            team1="Team 1",
            team2="Team 2",
            score="1 - 0",
            over="2.5",
            over_odds="0.60",
            match_id="1234567"
        )
