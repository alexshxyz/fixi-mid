import requests
import os
import json
from urllib.parse import quote
from dotenv import load_dotenv

from config import (
    BOT_TOKEN,
    CHANNEL_ID,
    SITE_URL,
    TELEGRAM_API_URL as TELEGRAM_API_URL_TEMPLATE,
    TELEGRAM_PROXY_HOST,
    TELEGRAM_PROXY_PASSWORD,
    TELEGRAM_PROXY_PORT,
    TELEGRAM_PROXY_USERNAME,
)
from storage import save_match, check_duplicate_match
from logger import setup_logger

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), '.env'))

logger = setup_logger(__name__)

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

TELEGRAM_API_URL = TELEGRAM_API_URL_TEMPLATE.format(token=BOT_TOKEN)
TELEGRAM_EDIT_MESSAGE_URL = TELEGRAM_API_URL.rsplit('/', 1)[0] + '/editMessageText'


# Приводит коэффициент к числу и прибавляет единицу для сообщения.
def _prepare_odds(over_odds):
    try:
        return round(float(over_odds) + 1, 2)
    except (ValueError, TypeError):
        return over_odds


# Нормализует отображение игрового времени в Telegram-сообщении.
def _normalize_match_time_for_message(raw_time):
    if raw_time is None:
        return 'Unknown'

    value = str(raw_time).strip()
    if not value or value == 'Unknown':
        return 'Unknown'

    upper = value.upper()
    if upper == 'HT':
        return '45'
    if upper == 'FT':
        return 'FT'
    if upper == 'ET':
        return 'ET'
    if upper.startswith('ET+'):
        return value
    return value


# Собирает текст прогноза для тотала или форы.
def _build_prediction(over, handicap_text, handicap_team_order):
    if handicap_text is None:
        return f"Over {over} FT"
    return f"Handicap {handicap_text} {handicap_team_order} FT"


# Форматирует прогноз и коэффициент с HTML-разметкой Telegram.
def _format_prediction_for_message(prediction, odds_value):
    return f"{prediction} · {odds_value}"


# Формирует HTML-текст уведомления о матче.
def _build_message(league, team1, team2, score, match_url, prediction, odds_value, match_time='Unknown'):
    clean_match_time = _normalize_match_time_for_message(match_time)

    # Make the team-name / score segment a hyperlink to the oddscomp detail page.
    # Telegram HTML parse mode supports <a href="...">...</a> for clickable text.
    match_text = f"{team1} {score} {team2}"
    linkified_match_text = f'<a href="{match_url}">{match_text}</a>' if match_url else match_text

    return (
        f"<b>{league}</b>\n"
        f"⚽️ {clean_match_time}’ {linkified_match_text}\n\n"
        f"{_format_prediction_for_message(prediction, odds_value)}"
    )


# Проверяет, отправлялся ли уже такой прогноз для матча.
def _is_duplicate_notification(match_url, prediction, match_id):
    if check_duplicate_match(match_url, prediction):
        return True
    return False


# Отправляет запрос в Telegram API и возвращает разобранный ответ.
def _send_message(payload, match_id=None, success_message=None, api_url=TELEGRAM_API_URL):
    try:
        response = requests.post(
            api_url,
            json=payload,
            proxies=TELEGRAM_PROXIES,
            timeout=10,
        )
        if response.status_code == 200:
            if success_message:
                logger.info(success_message)
            else:
                logger.info(f"Telegram notification sent for match {match_id}")
            try:
                return response.json()
            except ValueError:
                return {}

        logger.error(f"Failed to send Telegram notification: {response.text}")
    except Exception as e:
        logger.error(f"Error sending Telegram notification: {e}")
    return None


# Отправляет произвольное HTML-сообщение в Telegram-канал.
def send_telegram_message(text):
    payload = {
        "chat_id": CHANNEL_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    return bool(_send_message(payload, success_message="Telegram message sent"))


# Редактирует ранее отправленное сообщение в Telegram.
def edit_telegram_notification(message_id, text):
    payload = {
        "chat_id": CHANNEL_ID,
        "message_id": message_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    return bool(_send_message(
        payload,
        success_message=f"Telegram notification {message_id} updated",
        api_url=TELEGRAM_EDIT_MESSAGE_URL,
    ))


# Сохраняет отправленное уведомление в хранилище.
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
        logger.error(f"Failed to save match: {db_error}")


def send_telegram_notification(
    league,
    team1,
    team2,
    score,
    over=None,
    over_odds=None,
    match_id=None,
    handicap_text=None,
    handicap_team_order=None,
    match_time='Unknown',
    on_sent=None,
    on_sent_details=None,
):
    # Отправляет уведомление о матче в Telegram канал.
    match_url = f"{SITE_URL}oddscomp/{match_id}" if match_id else ""
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
        match_time=match_time,
    )

    payload = {
        "chat_id": CHANNEL_ID,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }

    if _is_duplicate_notification(match_url, prediction, match_id):
        return False

    telegram_response = _send_message(payload, match_id)
    if not telegram_response:
        return False

    _save_notification(league, team1, team2, prediction, odds_value, match_url)
    result = telegram_response.get('result')
    telegram_message_id = (
        result.get('message_id') if isinstance(result, dict) else None
    )
    if telegram_message_id is not None:
        if on_sent:
            try:
                on_sent(telegram_message_id, message)
            except Exception as callback_error:
                logger.error(f"Failed to register Telegram notification: {callback_error}")
        if on_sent_details:
            try:
                on_sent_details(telegram_message_id, message, match_url, prediction)
            except Exception as callback_error:
                logger.error(f"Failed to register Telegram notification details: {callback_error}")
    elif on_sent or on_sent_details:
        logger.error("Telegram response did not include a message_id for status tracking")
    return True



# Для тестирования
if __name__ == "__main__":
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
