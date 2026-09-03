import os
import sys
import time
import threading
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright
from parser import parse_and_monitor_match, load_state_from_json, PageRestartRequired
from stats import run_stats_service
from storage import init_storage
from logging_config import setup_logger

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), '.env'))

logger = setup_logger(__name__)


def _retry_page_action(page, action, action_name, max_retries=3, reload_before_retry=True):
    """Повторяет действие со страницей после ошибки."""
    for attempt in range(1, max_retries + 1):
        try:
            return action()
        except Exception as e:
            if attempt == max_retries:
                logger.error(
                    f"Failed to {action_name} after {max_retries} attempts: {e}"
                )
                raise

            logger.warning(
                f"Attempt {attempt} to {action_name} failed: {e}. Retrying..."
            )
            if reload_before_retry:
                try:
                    page.reload(
                        wait_until="domcontentloaded",
                        timeout=60000,
                    )
                except Exception as reload_error:
                    logger.warning(f"Failed to reload page before retry: {reload_error}")


def init_browser(p, max_navigation_retries=3):
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

    def open_page():
        page.goto(
            "https://live5.nowgoal26.com/",
            wait_until="domcontentloaded",
            timeout=60000,
        )

    try:
        _retry_page_action(
            page,
            open_page,
            "load page",
            max_retries=max_navigation_retries,
            reload_before_retry=False,
        )
    except Exception:
        browser.close()
        raise

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
    logger.info("Switching to Live...")
    page.locator("li#li_FilterLive").click()
    page.locator("table#table_live").wait_for(timeout=10000)
    logger.info("Switched to Live")


def select_crown(page):
    """Выбор компании Crown и ожидание обновления данных"""
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
            timeout=5000,
        )
    except Exception:
        time.sleep(1)

    logger.info("Crown selected and data updated")


def configure_odds_settings(page):
    """Настройка отображения odds через settings"""
    logger.info("Opening settings...")
    page.locator("span#settingBtn").click()
    page.wait_for_selector("input#otc_2", timeout=5000)

    page.locator("input#otc_2").set_checked(False)
    page.locator("input#otc_3").set_checked(True)

    page.evaluate("MM_showHideLayers('soccerSettingWin','','none');")
    logger.info("Settings configured")


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

                    const hasOdds = row.querySelector('p.odds1, p.odds3') !== null;

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
    init_storage()

    # Запуск сервиса статистики в отдельном потоке
    stats_thread = threading.Thread(target=run_stats_service, daemon=True)
    stats_thread.start()
    logger.info("Сервис статистики запущен в отдельном потоке")

    while True:
        with sync_playwright() as p:
            browser, page = init_browser(p)

            try:
                def prepare_page():
                    switch_to_live(page)
                    select_crown(page)
                    configure_odds_settings(page)

                _retry_page_action(page, prepare_page, "prepare page")
                saved_state = load_state_from_json()

                if saved_state:
                    parse_and_monitor_match(page, saved_state=saved_state)
                else:
                    matches = collect_matches(page)
                    while not matches:
                        logger.info("No matches found. Retrying in 30 seconds...")
                        time.sleep(30)

                        def reload_page():
                            page.reload(
                                wait_until="domcontentloaded",
                                timeout=60000,
                            )

                        _retry_page_action(page, reload_page, "reload page")
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