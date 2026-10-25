import os
import re
import signal
import sys
import threading
import time
from datetime import datetime
from functools import partial

if __package__ in (None, ""):
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    sys.path.insert(0, project_root)

from playwright.sync_api import sync_playwright
from app.tracker import (
    parse_and_monitor_match,
    load_state_from_json,
    PageRestartRequired,
)
from app.storage import init_storage
from app.config import (
    BROWSER_HEADLESS,
    BROWSER_PROXY_SERVER,
    DEBUGMODE,
    PROJECT_DIR,
    NO_MATCH_RETRY_DELAY_SECONDS,
    PAGE_ACTION_MAX_RETRIES,
    PAGE_ACTION_RETRY_DELAY_SECONDS,
    PAGE_DATA_READY_TIMEOUT_SECONDS,
    PAGE_ELEMENT_WAIT_TIMEOUT_MS,
    PAGE_LOAD_TIMEOUT_MS,
    PAGE_POLL_INTERVAL_MS,
    PAGE_REFRESH_TABLE_TIMEOUT_MS,
    RESTART_HOURS,
    SITE_COOKIES,
    SITE_URL,
)
from app.logger import setup_logger
from app.observer import _collect_match_status, _write_match_count
from app.scripts import HAS_VALID_MATCH_DATA

logger = setup_logger(__name__)
ERROR_SCREENSHOTS_DIR = os.path.join(
    str(PROJECT_DIR), "logs", "screenshots"
)


def _request_shutdown(shutdown_requested, _signum, _frame):
    shutdown_requested.set()


def _wait_for_shutdown(shutdown_requested, seconds):
    return shutdown_requested.wait(seconds)


def _capture_error_screenshot(page, error_label, error):
    try:
        os.makedirs(ERROR_SCREENSHOTS_DIR, exist_ok=True)
        error_detail = str(error).splitlines()[0] or error.__class__.__name__
        error_detail = error_detail.encode("ascii", "replace").decode("ascii")
        filename = (
            f"{datetime.now().strftime('%Y-%m-%d %H-%M-%S-%f')} - "
            f"ERROR - {error_label}: {error_detail}"
        )
        filename = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "-", filename)
        screenshot_path = os.path.join(
            ERROR_SCREENSHOTS_DIR, f"{filename[:200]}.png"
        )
        page.screenshot(path=screenshot_path)
        logger.info("Saved error screenshot: %s", screenshot_path)
    except Exception:
        logger.warning(
            "Failed to capture screenshot for %s",
            error_label,
            exc_info=True,
        )


# Повторяет действие со страницей после ошибки.
def _retry_page_action(
    page,
    action,
    action_name,
    max_retries=PAGE_ACTION_MAX_RETRIES,
    retry_delay=PAGE_ACTION_RETRY_DELAY_SECONDS,
    reload_before_retry=False,
):
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
            time.sleep(retry_delay)
            if reload_before_retry:
                try:
                    refresh_live_table(page)
                except Exception as reload_error:
                    logger.warning(f"Failed to refresh Live table before retry: {reload_error}")


# Инициализация браузера и страницы.
def init_browser(p, max_navigation_retries=PAGE_ACTION_MAX_RETRIES):
    logger.info("Setup browser...")
    if not BROWSER_PROXY_SERVER:
        raise RuntimeError("BROWSER_PROXY_SERVER is not configured")

    browser = p.chromium.launch(
        headless=BROWSER_HEADLESS,
        proxy={"server": BROWSER_PROXY_SERVER},
        args=[
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
        ],
    )
    context = browser.new_context()
    page = context.new_page()
    page.set_viewport_size({"width": 1280, "height": 720})

    try:
        context.add_cookies(SITE_COOKIES)
        logger.info("Cookies set")
    except Exception as e:
        logger.warning(f"Failed to add site cookies: {e}")

    def open_page():
        page.goto(
            SITE_URL,
            wait_until="domcontentloaded",
            timeout=PAGE_LOAD_TIMEOUT_MS,
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

    logger.info("Page loaded, OK")
    return browser, page


# Закрытие всплывающего окна.
def close_popup(page):
    try:
        logger.info("Waiting for popup close button...")
        page.locator("i.closebtn").wait_for(timeout=PAGE_ELEMENT_WAIT_TIMEOUT_MS)
        page.locator("i.closebtn").click()
        logger.info("Popup closed")
    except Exception as e:
        logger.info("Popup did not appear or could not be closed")


# Переключение на фильтр Live.
def switch_to_live(page):
    try:
        page.locator("li#li_FilterLive").wait_for(timeout=PAGE_ELEMENT_WAIT_TIMEOUT_MS)
        page.locator("li#li_FilterLive").click()
        page.locator("table#table_live").wait_for(timeout=PAGE_ELEMENT_WAIT_TIMEOUT_MS)
        logger.info("Switched to Live")
    except Exception as e:
        logger.error(f"Failed to switch to Live: {e}")
        raise


# Обновляет Live-таблицу кликом по фильтру без полной перезагрузки страницы.
def refresh_live_table(page):
    logger.info("Refreshing...")
    try:
        live_filter = page.locator("li#li_FilterLive")
        live_filter.wait_for(timeout=PAGE_ELEMENT_WAIT_TIMEOUT_MS)
        live_filter.click()
        page.locator("table#table_live").wait_for(timeout=PAGE_ELEMENT_WAIT_TIMEOUT_MS)
        page.wait_for_timeout(PAGE_POLL_INTERVAL_MS)
        logger.info("Live table refreshed")
    except Exception as e:
        logger.error("Failed to refresh Live table: %s", e)
        _capture_error_screenshot(page, "Failed to refresh Live table", e)
        raise


# Полностью перезагружает страницу и ждёт готовности данных Live.
def refresh_page(page, wait_for_data=True):
    logger.info("Refreshing...")
    try:
        page.reload(wait_until="domcontentloaded", timeout=PAGE_LOAD_TIMEOUT_MS)
        page.locator("table#table_live").wait_for(timeout=PAGE_REFRESH_TABLE_TIMEOUT_MS)

        if not wait_for_data:
            logger.info("Page refreshed")
            return

        deadline = time.monotonic() + PAGE_DATA_READY_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            if has_valid_match_data(page):
                page.wait_for_timeout(PAGE_POLL_INTERVAL_MS)
                logger.info("Page refreshed")
                return
            page.wait_for_timeout(PAGE_POLL_INTERVAL_MS)

        raise TimeoutError("Live table data did not become ready after page refresh")
    except Exception as e:
        logger.error(f"Failed to refresh page: {e}")
        _capture_error_screenshot(page, "Failed to refresh page", e)
        raise


# Сбор списка ID матчей.
def collect_matches(page):
    matches = []
    match_status = None
    try:
        page.wait_for_timeout(PAGE_POLL_INTERVAL_MS)
        match_status = _collect_match_status(page, require_crown=True)
        matches = list(dict.fromkeys(
            item['match_id'] for item in match_status if item['active']
        ))
        logger.info(f"Matches found: {len(matches)} ({', '.join(matches)})")
    except Exception:
        logger.exception("Failed to collect matches")
    if DEBUGMODE == 1 and match_status is not None:
        _write_match_count(match_status, matches)
    return matches


# Проверяем, что после загрузки доступны Crown и видимые odds.
def has_valid_match_data(page):
    page.wait_for_timeout(PAGE_POLL_INTERVAL_MS)
    return page.evaluate(HAS_VALID_MATCH_DATA)


# Полностью перезапускает процесс скрипта.
def _restart_script(browser=None):
    if browser is not None:
        try:
            browser.close()
        except Exception:
            pass
    os.execv(sys.executable, [sys.executable] + sys.argv)


def _restart_if_due(restart_deadline, browser, shutdown_requested=None):
    if shutdown_requested is not None and shutdown_requested.is_set():
        return
    if time.monotonic() < restart_deadline:
        return
    logger.info(
        "Restart interval reached (%s hours). Restarting script...",
        RESTART_HOURS,
    )
    if browser is None:
        _restart_script()
    raise PageRestartRequired(f"Scheduled restart after {RESTART_HOURS} hours")


# Главный цикл запуска бота.
def main():
    shutdown_requested = threading.Event()
    previous_handlers = {
        signum: signal.signal(
            signum, partial(_request_shutdown, shutdown_requested)
        )
        for signum in (signal.SIGINT, signal.SIGTERM)
    }
    try:
        restart_deadline = time.monotonic() + RESTART_HOURS * 3600
        init_storage()
        _restart_if_due(restart_deadline, None, shutdown_requested)

        while not shutdown_requested.is_set():
            with sync_playwright() as p:
                browser, page = init_browser(p)

                try:
                    _restart_if_due(restart_deadline, browser, shutdown_requested)
                    # Подготавливаем страницу перед запуском мониторинга.
                    _retry_page_action(
                        page,
                        lambda: switch_to_live(page),
                        "prepare page",
                    )
                    if shutdown_requested.is_set():
                        break
                    _restart_if_due(restart_deadline, browser, shutdown_requested)
                    # Всегда получаем актуальные матчи; состояние загружается после ожидания,
                    # чтобы оно оставалось на диске, если перезапуск наступит без матчей.
                    matches = collect_matches(page)
                    while not matches and not shutdown_requested.is_set():
                        _restart_if_due(restart_deadline, browser, shutdown_requested)
                        remaining_seconds = restart_deadline - time.monotonic()
                        logger.info(
                            "No matches found. Retrying in %s seconds...",
                            NO_MATCH_RETRY_DELAY_SECONDS,
                        )
                        _wait_for_shutdown(
                            shutdown_requested,
                            max(
                                0,
                                min(NO_MATCH_RETRY_DELAY_SECONDS, remaining_seconds),
                            ),
                        )
                        if shutdown_requested.is_set():
                            break
                        _restart_if_due(restart_deadline, browser, shutdown_requested)
                        refresh_page(page, wait_for_data=False)
                        matches = collect_matches(page)

                    if shutdown_requested.is_set():
                        break

                    _restart_if_due(restart_deadline, browser, shutdown_requested)
                    saved_state = load_state_from_json()
                    parse_and_monitor_match(
                        page,
                        matches,
                        saved_state=saved_state,
                        restart_deadline=restart_deadline,
                        live_refresh_callback=refresh_live_table,
                        page_refresh_callback=refresh_page,
                        shutdown_requested=shutdown_requested,
                    )
                except PageRestartRequired as e:
                    if shutdown_requested.is_set():
                        logger.info("Shutdown requested")
                        return
                    if not str(e).startswith("Scheduled restart after "):
                        logger.info(f"{e}. Restarting script...")
                    _restart_script(browser)
                except Exception as e:
                    if shutdown_requested.is_set():
                        logger.warning(
                            "Browser operation was interrupted during shutdown",
                            exc_info=True,
                        )
                        return
                    logger.error(f"Unexpected error in main: {e}")
                    try:
                        browser.close()
                    except Exception:
                        pass
                    raise
                finally:
                    if shutdown_requested.is_set():
                        try:
                            browser.close()
                            logger.info("Browser closed")
                        except Exception:
                            logger.error("Failed to close browser", exc_info=True)

            # Если execv подхватил, этот код не выполнится.
            if shutdown_requested.is_set():
                return
    finally:
        for signum, previous_handler in previous_handlers.items():
            signal.signal(signum, previous_handler)


if __name__ == "__main__":
    main()