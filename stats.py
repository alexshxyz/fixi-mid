"""
Автоматическая отправка месячной статистики в Telegram-канал.
"""
import sys
import os
import time
import logging
from datetime import datetime, timedelta
from dotenv import load_dotenv
import requests

# Добавляем родительскую директорию в path для импорта модулей
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import _get_connection as get_db_conn, TABLE_NAME

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), '.env'))

# Настройка логирования
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Handler для файла
log_file = os.path.join(os.path.dirname(__file__), 'stats.log')
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

# Файл для отслеживания отправленных статистик
STATS_FILE = os.path.join(os.path.dirname(__file__), 'stats.txt')


def get_last_month_stats():
    """
    Получает статистику матчей за прошлый месяц.

    Returns:
        str: отформатированный текст со статистикой за прошлый месяц
    """
    try:
        conn = get_db_conn()
        cur = conn.cursor()

        # Получаем текущую дату в UTC+3
        now_utc3 = datetime.utcnow() + timedelta(hours=3)
        current_year = now_utc3.year
        current_month = now_utc3.month

        # Определяем прошлый месяц
        if current_month == 1:
            last_month = 12
            last_year = current_year - 1
        else:
            last_month = current_month - 1
            last_year = current_year

        # Получаем первый день прошлого месяца и первый день текущего месяца
        first_day_last = datetime(last_year, last_month, 1).date()
        first_day_current = datetime(current_year, current_month, 1).date()

        # Названия месяцев в именительном падеже
        month_names = {
            1: "JANUARY", 2: "FEBRUARY", 3: "MARCH", 4: "APRIL",
            5: "MAY", 6: "JUNE", 7: "JULY", 8: "AUGUST",
            9: "SEPTEMBER", 10: "OCTOBER", 11: "NOVEMBER", 12: "DECEMBER"
        }
        last_month_name = month_names.get(last_month, "UNKNOWN MONTH")

        query = f"""
        SELECT
            COUNT(*) AS total_matches,
            SUM(CASE WHEN result = 'Won' THEN 1 ELSE 0 END) AS wins,
            SUM(CASE WHEN result = 'Lost' THEN 1 ELSE 0 END) AS losses,
            SUM(CASE WHEN result = 'Void' THEN 1 ELSE 0 END) AS voids
        FROM {TABLE_NAME}
        WHERE date IS NOT NULL AND date >= %s AND date < %s
        """

        cur.execute(query, (first_day_last, first_day_current))
        result = cur.fetchone()
        cur.close()
        conn.close()

        if result:
            total_matches, wins, losses, voids = result
            wins = wins or 0
            losses = losses or 0
            voids = voids or 0

            # Вычисляем WR (Win Rate) - исключаем Void
            matches_without_void = wins + losses
            if matches_without_void > 0:
                win_rate = int(wins * 100 / matches_without_void)
            else:
                win_rate = 0

            # Вычисляем прибыль (Won = +0.8, Lost = -1, Void = 0)
            profit = wins * 0.8 - losses * 1
            profit_str = f"+{profit:.2f}" if profit >= 0 else f"{profit:.2f}"

            stats_text = (
                f"📅 <b>{last_month_name}</b>\n\n"
                f"💰 {profit_str} units 📈 {win_rate}% WR\n\n"
                f"🎯 <b>{total_matches} matches</b>\n"
                f"{wins}W / {losses}L / {voids}D"
            )
            return stats_text
        else:
            return f"📅 <b>{last_month_name}</b>\n\nNo data available."
    except Exception as e:
        logger.error(f"Ошибка при получении статистики за прошлый месяц: {e}")
        return f"❌ Ошибка при получении статистики: {e}"


def send_telegram_message(text):
    """
    Отправляет сообщение в Telegram канал.

    Args:
        text (str): Текст сообщения

    Returns:
        bool: True если отправка успешна, False иначе
    """
    payload = {
        "chat_id": CHANNEL_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }

    try:
        response = requests.post(TELEGRAM_API_URL, json=payload, timeout=10)
        if response.status_code == 200:
            logger.info("Статистика успешно отправлена в Telegram")
            return True
        else:
            logger.error(f"Не удалось отправить статистику в Telegram: {response.text}")
            return False
    except Exception as e:
        logger.error(f"Ошибка при отправке статистики в Telegram: {e}")
        return False


def check_stats_sent(year, month):
    """
    Проверяет, была ли уже отправлена статистика за указанный месяц и год.

    Args:
        year (int): Год
        month (int): Месяц

    Returns:
        bool: True если статистика уже отправлена, False иначе
    """
    if not os.path.exists(STATS_FILE):
        return False

    try:
        with open(STATS_FILE, 'r', encoding='utf-8') as f:
            sent_stats = f.read().splitlines()
        period = f"{year}-{month:02d}"
        return f"STATS SENT {period}" in sent_stats
    except Exception as e:
        logger.error(f"Ошибка при проверке файла stats.txt: {e}")
        return False


def mark_stats_sent(year, month):
    """
    Отмечает, что статистика за указанный месяц и год была отправлена.

    Args:
        year (int): Год
        month (int): Месяц
    """
    try:
        with open(STATS_FILE, 'a', encoding='utf-8') as f:
            period = f"{year}-{month:02d}"
            f.write(f"STATS SENT {period}\n")
        logger.info(f"Статистика за {period} отмечена как отправленная")
    except Exception as e:
        logger.error(f"Ошибка при записи в файл stats.txt: {e}")


def check_and_send_monthly_stats():
    """
    Проверяет условия и отправляет месячную статистику, если необходимо.
    """
    # Получаем текущую дату в UTC+3
    now_utc3 = datetime.utcnow() + timedelta(hours=3)
    current_day = now_utc3.day

    # Отправляем статистику только 1 числа каждого месяца (для тестирования)
    if current_day != 1:
        logger.info("Сегодня не 1 число месяца, пропускаем отправку статистики")
        return

    # Определяем прошлый месяц
    if now_utc3.month == 1:
        last_month = 12
        last_year = now_utc3.year - 1
    else:
        last_month = now_utc3.month - 1
        last_year = now_utc3.year

    # Проверяем, была ли уже отправлена статистика за прошлый месяц
    if check_stats_sent(last_year, last_month):
        logger.info(f"Статистика за {last_year}-{last_month:02d} уже была отправлена")
        return

    # Получаем статистику за прошлый месяц
    stats_text = get_last_month_stats()

    # Отправляем в Telegram
    if send_telegram_message(stats_text):
        # Отмечаем как отправленную
        mark_stats_sent(last_year, last_month)
    else:
        logger.error("Не удалось отправить статистику в Telegram")


def run_stats_service():
    """
    Основной цикл сервиса статистики.
    Проверяет условия раз в сутки.
    """
    logger.info("Запуск сервиса автоматической отправки месячной статистики")

    while True:
        try:
            check_and_send_monthly_stats()
        except Exception as e:
            logger.error(f"Ошибка в сервисе статистики: {e}")

        # Ждем 24 часа (86400 секунд)
        time.sleep(86400)


if __name__ == "__main__":
    run_stats_service()