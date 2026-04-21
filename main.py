import os
import sys
import logging
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright
from parser import parse_and_monitor_match, load_state_from_json, PageRestartRequired
from database import init_db

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


def init_browser(p):
    """Инициализация браузера и страницы"""
    logger.info("Initializing browser...")
    browser = p.chromium.launch(headless=True, args=[
        "--disable-gpu",
        "--disable-dev-shm-usage",
        "--no-sandbox",
        "--disable-setuid-sandbox",
        "--disable-infobars",
        "--disable-notifications",
        "--disable-background-networking",
        "--disable-background-timer-throttling",
        "--disable-renderer-backgrounding",
        "--disable-extensions",
        "--disable-sync",
        "--metrics-recording-only",
        "--mute-audio",
    ])
    page = browser.new_page()
    page.set_viewport_size({"width": 1280, "height": 720})
    page.goto("https://live5.nowgoal26.com/")
    page.locator("i.closebtn").wait_for(timeout=15000)  # Ждем появления popup для подтверждения загрузки
    logger.info("Page loaded successfully")
    return browser, page


def close_popup(page):
    """Закрытие всплывающего окна"""
    try:
        logger.info("Waiting for popup close button...")
        page.locator("i.closebtn").wait_for(timeout=10000)
        page.locator("i.closebtn").click()
        logger.info("Popup closed")
    except Exception as e:
        logger.info("Popup did not appear or could not be closed")


def switch_to_live(page):
    """Переключение на фильтр Live"""
    try:
        logger.info("Switching to Live...")
        page.locator("li#li_FilterLive").click()
        page.locator("table#table_live").wait_for(timeout=10000)  # Ждем появления таблицы live
        logger.info("Switched to Live")
    except Exception as e:
        logger.info("Failed to switch to Live")


def select_crown(page):
    """Выбор компании Crown и ожидание обновления данных"""
    try:
        logger.info("Selecting company Crown...")
        select = page.locator("select#CompanySel")

        select.select_option(value="3")
        page.wait_for_function(
            "() => { const select = document.querySelector('#CompanySel'); return select && select.value === '3'; }",
            timeout=5000,
        )

        previous_rows = page.evaluate("""
            () => Array.from(document.querySelectorAll('table#table_live tbody tr.tds'))
                .map(row => row.innerText.trim()).join('||')
        """)

        try:
            page.wait_for_function(
                "prev => { const rows = Array.from(document.querySelectorAll('table#table_live tbody tr.tds')); const snapshot = rows.map(row => row.innerText.trim()).join('||'); return snapshot !== prev; }",
                arg=previous_rows,
                timeout=2000,
            )
        except Exception:
            import time
            time.sleep(1)

        logger.info("Crown selected and data updated")
    except Exception as e:
        logger.info("Failed to select Crown")


def configure_odds_settings(page):
    """Настройка отображения odds через settings"""
    try:
        logger.info("Opening settings...")
        page.locator("span#settingBtn").click()
        page.wait_for_selector("input#otc_2", timeout=5000)  # Ждем появления чекбоксов

        page.locator("input#otc_2").set_checked(False)  # Снять чекбокс otc_2
        page.locator("input#otc_3").set_checked(True)   # Включить чекбокс otc_3

        # Закрыть окно настроек
        page.evaluate("MM_showHideLayers('soccerSettingWin','','none');")
        logger.info("Settings configured")
    except Exception as e:
        logger.info("Failed to configure settings")


def collect_matches(page):
    """Сбор списка ID матчей"""
    matches = []
    try:
        logger.info("Counting matches with odds...")
        matches = page.evaluate("""
            () => {
                const matches = [];
                const timeElements = Array.from(document.querySelectorAll('[id^="time_"]'));

                for (const timeElem of timeElements) {
                    if (timeElem.offsetParent === null) continue;
                    const matchId = timeElem.id.replace(/^time_/, '');
                    if (!matchId) continue;

                    const row = timeElem.closest('tr');
                    if (!row) continue;

                    const hasOdds = row.querySelector('p.odds1, p.odds2') !== null;

                    if (hasOdds) {
                        matches.push(matchId);
                    }
                }

                return matches;
            }
        """)
        logger.info(f"Found {len(matches)} matches")
    except Exception as e:
        logger.info("Failed to count matches with odds")
    return matches


def main():
    init_db()

    while True:
        with sync_playwright() as p:
            browser, page = init_browser(p)
            close_popup(page)
            switch_to_live(page)
            select_crown(page)
            configure_odds_settings(page)
            saved_state = load_state_from_json()

            try:
                if saved_state:
                    parse_and_monitor_match(page, saved_state=saved_state)
                else:
                    matches = collect_matches(page)
                    parse_and_monitor_match(page, matches)
            except PageRestartRequired as e:
                logger.warning(f"{e}. Restarting script after saving state...")
                try:
                    browser.close()
                except Exception:
                    pass
                os.execv(sys.executable, [sys.executable] + sys.argv)
            except Exception as e:
                logger.error(f"Unexpected error in main: {e}")
                try:
                    browser.close()
                except Exception:
                    pass
                raise

        # Если execv подхватил, этот код не выполнится.


if __name__ == "__main__":
    main()