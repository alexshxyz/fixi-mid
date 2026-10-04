import os
import sys
import time
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright
from tracker import (
    parse_and_monitor_match,
    load_state_from_json,
    PageRestartRequired,
)
from storage import init_storage
from config import BROWSER_HEADLESS, DEBUGMODE, SITE_COOKIES, SITE_URL
from logger import setup_logger
from observer import _collect_match_status, _write_match_count

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), '.env'))

logger = setup_logger(__name__)


# Повторяет действие со страницей после ошибки.
def _retry_page_action(page, action, action_name, max_retries=3, reload_before_retry=False):
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
                    refresh_live_table(page)
                except Exception as reload_error:
                    logger.warning(f"Failed to refresh Live table before retry: {reload_error}")


# Инициализация браузера и страницы.
def init_browser(p, max_navigation_retries=3):
    logger.info("Setup browser...")
    browser = p.chromium.launch(headless=BROWSER_HEADLESS, args=[
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
    context = browser.new_context()
    page = context.new_page()
    page.set_viewport_size({"width": 1280, "height": 720})

    try:
        context.add_cookies(SITE_COOKIES)
        logger.info("Cookies loaded")
    except Exception as e:
        logger.warning(f"Failed to add site cookies: {e}")

    def open_page():
        page.goto(
            SITE_URL,
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


# Закрытие всплывающего окна.
def close_popup(page):
    try:
        logger.info("Waiting for popup close button...")
        page.locator("i.closebtn").wait_for(timeout=10000)
        page.locator("i.closebtn").click()
        logger.info("Popup closed")
    except Exception as e:
        logger.info("Popup did not appear or could not be closed")


# Переключение на фильтр Live.
def switch_to_live(page):
    try:
        page.locator("li#li_FilterLive").wait_for(timeout=10000)
        page.locator("li#li_FilterLive").click()
        page.locator("table#table_live").wait_for(timeout=10000)
        logger.info("Switched to Live")
    except Exception as e:
        logger.error(f"Failed to switch to Live: {e}")
        raise


# Обновляет Live-таблицу кликом по фильтру без полной перезагрузки страницы.
def refresh_live_table(page):
    logger.info("Refreshing...")
    try:
        live_filter = page.locator("li#li_FilterLive")
        live_filter.wait_for(timeout=10000)
        live_filter.click()
        page.locator("table#table_live").wait_for(timeout=10000)
        page.wait_for_timeout(1000)
        logger.info("Live table refreshed")
    except Exception as e:
        logger.error("Failed to refresh Live table: %s", e)
        raise


# Полностью перезагружает страницу и ждёт готовности данных Live.
def refresh_page(page):
    logger.info("Refreshing...")
    try:
        page.reload(wait_until="domcontentloaded", timeout=60000)
        page.locator("table#table_live").wait_for(timeout=10000)

        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            if has_valid_match_data(page):
                logger.info("Page refreshed")
                return
            page.wait_for_timeout(1000)

        raise TimeoutError("Live table data did not become ready after page refresh")
    except Exception as e:
        logger.error(f"Failed to refresh page: {e}")
        raise


# Сбор списка ID матчей.
def collect_matches(page):
    matches = []
    match_status = None
    try:
        page.wait_for_timeout(1000)
        match_status = _collect_match_status(page, require_crown=True)
        matches = list(dict.fromkeys(
            item['match_id'] for item in match_status if item['active']
        ))
        logger.info(f"Matches found: {len(matches)} ({', '.join(matches)})")
    except Exception:
        pass
    if DEBUGMODE == 1 and match_status is not None:
        _write_match_count(match_status, matches)
    return matches


# Проверяем, что после загрузки доступны Crown и видимые odds.
def has_valid_match_data(page):
    page.wait_for_timeout(1000)
    return page.evaluate("""
        () => {
            const hasCrownOdds = Array.from(
                document.querySelectorAll('td.oddstd[onclick]')
            ).some(cell => /,\\s*["']3["']\\s*,/.test(
                cell.getAttribute('onclick') || ''
            ));

            const hasVisibleOdds = Array.from(
                document.querySelectorAll('td.oddstd')
            ).some(cell => {
                if (cell.offsetParent === null) return false;
                const odds1 = cell.querySelector('p.odds1');
                const odds3 = cell.querySelector('p.odds3');
                return (odds1 && odds1.offsetParent !== null) ||
                    (odds3 && odds3.offsetParent !== null);
            });

            return hasCrownOdds && hasVisibleOdds;
        }
    """)


# Главный цикл запуска бота.
def main():
    init_storage()

    while True:
        with sync_playwright() as p:
            browser, page = init_browser(p)

            try:
                preparation_steps = [
                    ("switch_to_live", lambda: switch_to_live(page)),
                ]
                preparation_step = 0

                # Подготавливаем страницу перед запуском мониторинга.
                def prepare_page():
                    nonlocal preparation_step

                    while preparation_step < len(preparation_steps):
                        step_name, step_action = preparation_steps[preparation_step]
                        try:
                            step_action()
                        except Exception as e:
                            logger.error(f"Failed preparation step {step_name}: {e}")
                            raise
                        preparation_step += 1

                _retry_page_action(page, prepare_page, "prepare page")
                saved_state = load_state_from_json()

                # Если есть сохранённое состояние, ждём готовности данных.
                if saved_state:
                    while not has_valid_match_data(page):
                        logger.info("Saved-state data is not ready. Retrying in 30 seconds...")
                        time.sleep(30)

                        # Обновляем Live-таблицу, пока данные не станут доступны.
                        def reload_page():
                            refresh_live_table(page)

                        _retry_page_action(page, reload_page, "refresh live table")
                    parse_and_monitor_match(
                        page,
                        saved_state=saved_state,
                        live_refresh_callback=refresh_live_table,
                        page_refresh_callback=refresh_page,
                    )
                else:
                    # Собираем матчи и ждём, пока они появятся.
                    matches = collect_matches(page)
                    while not matches:
                        logger.info("No matches found. Retrying in 60 seconds...")
                        time.sleep(60)

                        # Обновляем Live-таблицу и проверяем, появились ли матчи.
                        def reload_page():
                            refresh_live_table(page)

                        _retry_page_action(page, reload_page, "refresh live table")
                        matches = collect_matches(page)

                    parse_and_monitor_match(
                        page,
                        matches,
                        live_refresh_callback=refresh_live_table,
                        page_refresh_callback=refresh_page,
                    )
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