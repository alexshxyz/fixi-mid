import requests
import os
import logging
from dotenv import load_dotenv

from database import save_match, check_duplicate_match

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), '.env'))

# Настройка логирования
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Handler для файла
log_file = os.path.join(os.path.dirname(__file__), 'bot.log')
file_handler = logging.FileHandler(log_file)
file_handler.setLevel(logging.INFO)
file_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
file_handler.setFormatter(file_formatter)

# Handler для консоли
console_handler = logging.StreamHandler()
console_handler.setLevel(logging.INFO)
console_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
console_handler.setFormatter(console_formatter)

logger.addHandler(file_handler)
logger.addHandler(console_handler)

# Настройки для Telegram
BOT_TOKEN = os.environ.get('BOT_TOKEN')
CHANNEL_ID = os.environ.get('CHANNEL_ID')

TELEGRAM_API_URL = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"


def send_telegram_notification(league, team1, team2, score, over=None, over_odds=None, match_id=None, handicap_text=None, handicap_team_order=None):
    """
    Отправляет уведомление о матче в Telegram канал
    """
    match_url = f"https://live5.nowgoal26.com/oddscomp/{match_id}" if match_id else ""

    try:
        odds_value = round(float(over_odds) + 1, 2)
    except (ValueError, TypeError):
        odds_value = over_odds

    if handicap_text is None:
        extra_line = f"<b>Over {over} FT</b>\n"
    else:
        extra_line = f"<b>Handicap {handicap_text} {handicap_team_order} FT</b>\n"

    message = (
        "<b>⭐️ Crown</b>\n"
        f"{league}\n"
        f"<b><a href=\"{match_url}\">{team1} {score} {team2}</a></b>\n"
        f"{extra_line}"
        f"Odds {odds_value}"
    )
    
    payload = {
        "chat_id": CHANNEL_ID,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }
    
    prediction = (
        f"Over {over} FT"
        if handicap_text is None
        else f"Handicap {handicap_text} {handicap_team_order} FT"
    )
    
    # Проверяем дубликат перед отправкой
    if check_duplicate_match(match_url, prediction):
        logger.info(f"[DUPLICATE] Match {match_id} with prediction '{prediction}' already sent. Skipping.")
        return False
    
    try:
        response = requests.post(TELEGRAM_API_URL, json=payload, timeout=10)
        if response.status_code == 200:
            logger.info(f"Telegram notification sent for match {match_id}")
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
            return True
        else:
            logger.error(f"Failed to send Telegram notification: {response.text}")
            return False
    except Exception as e:
        logger.error(f"Error sending Telegram notification: {e}")
        return False


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
