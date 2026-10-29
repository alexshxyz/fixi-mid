import os
import re
import signal
import sys
import threading
import time
from collections.abc import Callable
from contextlib import suppress
from datetime import datetime
from functools import partial
from types import FrameType
from typing import NoReturn

if __package__ in (None, ""):
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    sys.path.insert(0, project_root)

from playwright.sync_api import Browser, Page, Playwright, sync_playwright
from app.tracker import parse_and_monitor_match
from app.state import load_state_from_json
from app.storage import init_storage
from app.config import (
    BROWSER_HEADLESS,
    BROWSER_PROXY_SERVER,
    BOT_TOKEN,
    CHANNEL_ID,
    DEBUGMODE,
    ERROR_SCREENSHOTS_DIR,
    HARD_PAGE_REFRESH_MAX_RETRIES,
    HARD_PAGE_REFRESH_RETRY_DELAY_SECONDS,
    NO_MATCH_RETRY_DELAY_SECONDS,
    PAGE_ACTION_MAX_RETRIES,
    PAGE_ACTION_RETRY_DELAY_SECONDS,
    PAGE_DATA_READY_TIMEOUT_SECONDS,
    PAGE_ELEMENT_WAIT_TIMEOUT_MS,
    PAGE_LOAD_MAX_RETRIES,
    PAGE_LOAD_TIMEOUT_MS,
    PAGE_PREPARE_MAX_RETRIES,
    PAGE_POLL_INTERVAL_MS,
    PAGE_REFRESH_TABLE_TIMEOUT_MS,
    RESTART_HOURS,
    SITE_COOKIES,
    SITE_URL,
    STATE_SAVE_FILE,
    validate_browser_proxy_server,
)
from app.logger import setup_logger
from app.observer import (
    collect_match_status,
    page_is_closed,
    write_match_count,
)
from app.scripts import LIVE_ODDS_READINESS

logger = setup_logger(__name__)


def _request_shutdown(
    shutdown_requested: threading.Event,
    _signum: int,
    _frame: FrameType | None,
) -> None:
    """Установить флаг завершения при получении системного сигнала."""
    shutdown_requested.set()


def _wait_for_shutdown(
    shutdown_requested: threading.Event, seconds: float
) -> bool:
    """Ожидать завершения работы или истечения указанного времени."""
    return shutdown_requested.wait(seconds)


def _capture_error_screenshot(page: Page, error_label: str, error: Exception) -> None:
    """Сохранить снимок страницы с контекстом ошибки в имени файла."""
    if page_is_closed(page, error):
        return
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


def _retry_page_action(
    page: Page,
    action: Callable[[], object],
    action_name: str,
    max_retries: int = PAGE_ACTION_MAX_RETRIES,
    retry_delay: float = PAGE_ACTION_RETRY_DELAY_SECONDS,
    reload_before_retry: bool = False,
    shutdown_requested: threading.Event | None = None,
) -> object | None:
    """Повторить действие со страницей после ошибки."""
    for attempt in range(1, max_retries + 1):
        _raise_if_shutdown(shutdown_requested, action_name)
        try:
            result = action()
            _raise_if_shutdown(shutdown_requested, action_name)
            return result
        except PageRestartRequired:
            raise
        except Exception as e:
            _raise_if_shutdown(shutdown_requested, action_name, e)
            if attempt == max_retries:
                logger.error(
                    f"Failed to {action_name} after {max_retries} attempts: {e}"
                )
                raise

            logger.warning(
                f"Attempt {attempt} to {action_name} failed: {e}. Retrying..."
            )
            if shutdown_requested is None:
                time.sleep(retry_delay)
            else:
                _wait_for_shutdown(shutdown_requested, retry_delay)
            _raise_if_shutdown(shutdown_requested, action_name, e)
            if reload_before_retry:
                try:
                    refresh_live_table(page)
                except Exception as reload_error:
                    _raise_if_shutdown(
                        shutdown_requested, action_name, reload_error
                    )
                    logger.warning(f"Failed to refresh Live table before retry: {reload_error}")
    return None


def init_browser(
    p: Playwright,
    max_navigation_retries: int = PAGE_LOAD_MAX_RETRIES,
    shutdown_requested: threading.Event | None = None,
) -> tuple[Browser, Page]:
    """Инициализировать браузер и открыть страницу сайта."""
    _raise_if_shutdown(shutdown_requested, "browser setup")

    logger.info("Setup browser...")
    proxy_server = validate_browser_proxy_server(BROWSER_PROXY_SERVER)
    try:
        browser = p.chromium.launch(
            headless=BROWSER_HEADLESS,
            proxy={"server": proxy_server},
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
    except Exception as e:
        _raise_if_shutdown(shutdown_requested, "browser launch", e)
        raise
    try:
        _raise_if_shutdown(shutdown_requested, "browser setup")
        context = browser.new_context()
        _raise_if_shutdown(shutdown_requested, "browser setup")
        page = context.new_page()
        _raise_if_shutdown(shutdown_requested, "browser setup")
        page.set_viewport_size({"width": 1280, "height": 720})
        _raise_if_shutdown(shutdown_requested, "browser setup")
        try:
            context.add_cookies(SITE_COOKIES)
            logger.info("Cookies set")
        except Exception as e:
            logger.warning(f"Failed to add site cookies: {e}")
        _raise_if_shutdown(shutdown_requested, "browser setup")
    except PageRestartRequired:
        with suppress(Exception):
            browser.close()
        raise
    except Exception:
        with suppress(Exception):
            browser.close()
        _raise_if_shutdown(shutdown_requested, "browser setup")
        raise

    def open_page() -> None:
        """Открыть сайт с заданным таймаутом загрузки."""
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
            shutdown_requested=shutdown_requested,
        )
    except PageRestartRequired:
        with suppress(Exception):
            browser.close()
        raise
    except Exception:
        logger.error(
            "Page load failed after %s attempts. Restarting script...",
            max_navigation_retries,
        )
        _restart_script(browser)
        raise

    logger.info("Page loaded, OK")
    return browser, page


def switch_to_live(page: Page) -> None:
    """Переключить страницу на фильтр Live."""
    try:
        page.locator("li#li_FilterLive").wait_for(timeout=PAGE_ELEMENT_WAIT_TIMEOUT_MS)
        page.locator("li#li_FilterLive").click()
        page.locator("table#table_live").wait_for(timeout=PAGE_ELEMENT_WAIT_TIMEOUT_MS)
        logger.info("Switched to Live")
    except Exception as e:
        logger.error(f"Failed to switch to Live: {e}")
        raise


def refresh_live_table(page: Page) -> None:
    """Обновить Live-таблицу кликом по фильтру."""
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


def refresh_page(page: Page, wait_for_data: bool = True) -> None:
    """Перезагрузить страницу и при необходимости дождаться данных Live."""
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
                logger.info("Page refreshed")
                return
            page.wait_for_timeout(PAGE_POLL_INTERVAL_MS)

        raise TimeoutError("Live table data did not become ready after page refresh")
    except Exception as e:
        logger.error(f"Failed to refresh page: {e}")
        _capture_error_screenshot(page, "Failed to refresh page", e)
        raise


def collect_matches(page: Page) -> list[str]:
    """Собрать ID активных матчей, готовых к наблюдению."""
    matches = []
    match_status = None
    try:
        page.wait_for_timeout(PAGE_POLL_INTERVAL_MS)
        match_status = collect_match_status(page, require_crown=True)
        matches = list(dict.fromkeys(
            item['match_id'] for item in match_status if item['active']
        ))
        logger.info(f"Matches found: {len(matches)} ({', '.join(matches)})")
    except Exception:
        logger.exception("Failed to collect matches")
    if DEBUGMODE and match_status is not None:
        write_match_count(match_status, matches)
    return matches


def has_valid_match_data(page: Page) -> bool:
    """Проверить готовность данных Crown и видимых коэффициентов."""
    page.wait_for_timeout(PAGE_POLL_INTERVAL_MS)
    readiness = page.evaluate(LIVE_ODDS_READINESS)
    return bool(readiness["hasCrownOdds"] and readiness["hasVisibleOdds"])


def _restart_script(browser: Browser | None = None) -> NoReturn:
    """Закрыть браузер и перезапустить процесс."""
    if browser is not None:
        with suppress(Exception):
            browser.close()
    os.execv(sys.executable, [sys.executable] + sys.argv)


def _restart_if_due(
    restart_deadline: float,
    browser: Browser | None,
    shutdown_requested: threading.Event | None = None,
) -> None:
    """Перезапустить браузер или процесс по истечении срока."""
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
    raise ScheduledRestartRequired(
        f"Scheduled restart after {RESTART_HOURS} hours"
    )


def _validate_configuration() -> None:
    """Проверить наличие обязательных настроек перед запуском."""
    required_settings = {
        "BOT_TOKEN": BOT_TOKEN,
        "CHANNEL_ID": CHANNEL_ID,
    }
    missing_settings = [
        name
        for name, value in required_settings.items()
        if not value or not value.strip()
    ]
    try:
        validate_browser_proxy_server(BROWSER_PROXY_SERVER)
    except RuntimeError:
        missing_settings.append("BROWSER_PROXY_SERVER")
    if missing_settings:
        raise RuntimeError(
            "Missing required configuration: " + ", ".join(missing_settings)
        )


def main() -> None:
    """Проверить конфигурацию и запустить основной цикл бота."""
    _validate_configuration()
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
                try:
                    browser, page = init_browser(
                        p, shutdown_requested=shutdown_requested
                    )
                except PageRestartRequired:
                    if shutdown_requested.is_set():
                        logger.info("Shutdown requested during browser setup")
                        return
                    raise

                try:
                    _restart_if_due(restart_deadline, browser, shutdown_requested)
                    # Подготавливаем страницу перед запуском мониторинга.
                    try:
                        _retry_page_action(
                            page,
                            lambda: switch_to_live(page),
                            "prepare page",
                            max_retries=PAGE_PREPARE_MAX_RETRIES,
                            shutdown_requested=shutdown_requested,
                        )
                    except Exception:
                        if shutdown_requested.is_set():
                            logger.info("Shutdown requested before Live setup finished")
                            break
                        logger.error(
                            "Failed to prepare page after %s attempts. "
                            "Restarting script...",
                            PAGE_PREPARE_MAX_RETRIES,
                        )
                        _restart_script(browser)
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
                        try:
                            _retry_page_action(
                                page,
                                lambda: refresh_page(page, wait_for_data=False),
                                "refresh page while waiting for matches",
                                max_retries=HARD_PAGE_REFRESH_MAX_RETRIES,
                                retry_delay=HARD_PAGE_REFRESH_RETRY_DELAY_SECONDS,
                                shutdown_requested=shutdown_requested,
                            )
                        except Exception:
                            if shutdown_requested.is_set():
                                logger.info(
                                    "Shutdown requested while refreshing page "
                                    "before matches appeared"
                                )
                                break
                            logger.error(
                                "Page refresh failed after %s attempts while "
                                "waiting for matches. Restarting script...",
                                HARD_PAGE_REFRESH_MAX_RETRIES,
                            )
                            _restart_script(browser)
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
                        saved_state_path=(
                            STATE_SAVE_FILE if saved_state is not None else None
                        ),
                    )
                except PageRestartRequired as e:
                    if shutdown_requested.is_set():
                        logger.info("Shutdown requested")
                        return
                    if not isinstance(e, ScheduledRestartRequired):
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
                    with suppress(Exception):
                        browser.close()
                    raise
                finally:
                    if shutdown_requested.is_set():
                        try:
                            browser.close()
                            logger.info("Browser closed")
                        except Exception as e:
                            if shutdown_requested.is_set():
                                logger.info(
                                    "Browser connection was already closed "
                                    "during shutdown: %s",
                                    e,
                                )
                            else:
                                logger.error(
                                    "Failed to close browser",
                                    exc_info=True,
                                )

            # Если execv подхватил, этот код не выполнится.
            if shutdown_requested.is_set():
                return
    finally:
        for signum, previous_handler in previous_handlers.items():
            signal.signal(signum, previous_handler)


if __name__ == "__main__":
    main()