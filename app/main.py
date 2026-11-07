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

from app import config
from playwright.sync_api import Browser, Page, Playwright, sync_playwright
from app.monitoring.tracker import parse_and_monitor_match
from app.persistence.state import load_state_from_json
from app.persistence.storage import init_storage
from app.config import (
    BROWSER_HEADLESS,
    DEBUGMODE,
    ERROR_SCREENSHOTS_DIR,
    FIRST_PAGE_LOAD_RETRY_DELAY_SECONDS,
    LIVE_TABLE_REFRESH_RETRY_DELAY_SECONDS,
    NO_MATCHES_RETRY_DELAY_SECONDS,
    PAGE_ELEMENT_WAIT_TIMEOUT_MS,
    PAGE_DOM_LOAD_MAX_ATTEMPTS,
    PAGE_DOM_LOAD_TIMEOUT_MS,
    PAGE_REFRESH_MAX_ATTEMPTS,
    PAGE_REFRESH_RETRY_DELAY_SECONDS,
    PREPARE_LIVE_TABLE_MAX_ATTEMPTS,
    HARDCODE_DELAY_MS,
    RESTART_HOURS,
    SITE_COOKIES,
    SITE_URL,
    STATE_SAVE_FILE,
    Settings,
)
from app.logger import setup_logger
from app.monitoring.observer import (
    PageRestartRequired,
    ScheduledRestartRequired,
    raise_if_shutdown,
    collect_crown_matches_status,
    page_is_closed,
    write_match_count,
)

logger = setup_logger(__name__)


class CookieSetupError(RuntimeError):
    """Сообщить о невозможности установить обязательные cookies."""


def _close_browser_quietly(browser: Browser) -> None:
    """Закрыть браузер, не скрывая исходную ошибку ошибкой закрытия."""
    with suppress(Exception):
        browser.close()


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
    if not getattr(error, "_fixi_error_logged", False):
        logger.error("%s: %s", error_label, error)
        setattr(error, "_fixi_error_logged", True)
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
        logger.info(
            "Saved error screenshot to %s",
            os.path.join(ERROR_SCREENSHOTS_DIR, ""),
        )
    except Exception:
        logger.warning(
            "Failed to capture screenshot for %s",
            error_label,
            exc_info=True,
        )


def _run_actions_with_retries(
    action: Callable[[], object],
    action_name: str,
    max_attempts: int,
    retry_delay: float,
    shutdown_requested: threading.Event | None = None,
) -> object | None:
    """Повторяет действия при: первоначальной загрузке сайта, переключении на live-фильтр и обновлении страницы при отсутствии матчей."""
    for attempt in range(1, max_attempts + 1):
        raise_if_shutdown(shutdown_requested, action_name)
        try:
            result = action()
            raise_if_shutdown(shutdown_requested, action_name)
            return result
        except PageRestartRequired:
            raise
        except Exception as e:
            raise_if_shutdown(shutdown_requested, action_name, e)
            if attempt == 1 and not getattr(e, "_fixi_error_logged", False):
                logger.error("Failed to %s: %s", action_name, e)
                setattr(e, "_fixi_error_logged", True)
            message = f"Attempt {attempt} to {action_name} failed"
            if attempt == max_attempts:
                logger.error(message)
                logger.error(
                    "Failed to %s after %s attempts",
                    action_name,
                    max_attempts,
                )
                raise

            logger.error("%s. Retrying...", message)
            if shutdown_requested is None:
                time.sleep(retry_delay)
            else:
                _wait_for_shutdown(shutdown_requested, retry_delay)
            raise_if_shutdown(shutdown_requested, action_name, e)
    return None


def init_browser(
    p: Playwright,
    max_navigation_attempts: int = PAGE_DOM_LOAD_MAX_ATTEMPTS,
    shutdown_requested: threading.Event | None = None,
    *,
    settings: Settings | None = None,
) -> tuple[Browser, Page]:
    """Инициализировать браузер и открыть страницу сайта."""
    raise_if_shutdown(shutdown_requested, "browser setup")
    runtime_settings = settings if settings is not None else config.load_settings()

    logger.info("Setup browser...")
    if not runtime_settings.browser_proxy_server.strip():
        raise RuntimeError("BROWSER_PROXY_SERVER is not configured")
    try:
        browser = p.chromium.launch(
            headless=BROWSER_HEADLESS,
            proxy={"server": runtime_settings.browser_proxy_server},
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
        raise_if_shutdown(shutdown_requested, "browser launch", e)
        raise
    try:
        raise_if_shutdown(shutdown_requested, "browser setup")
        context = browser.new_context()
        raise_if_shutdown(shutdown_requested, "browser setup")
        page = context.new_page()
        raise_if_shutdown(shutdown_requested, "browser setup")
        page.set_viewport_size({"width": 1280, "height": 720})
        raise_if_shutdown(shutdown_requested, "browser setup")
        try:
            context.add_cookies(SITE_COOKIES)
            logger.info("Cookies set")
        except Exception as e:
            raise CookieSetupError(
                f"Failed to add required site cookies: {e}"
            ) from e
        raise_if_shutdown(shutdown_requested, "browser setup")
    except PageRestartRequired:
        _close_browser_quietly(browser)
        raise
    except Exception:
        _close_browser_quietly(browser)
        raise_if_shutdown(shutdown_requested, "browser setup")
        raise

    def open_page() -> None:
        """Открыть сайт с заданным таймаутом загрузки."""
        page.goto(
            SITE_URL,
            wait_until="domcontentloaded",
            timeout=PAGE_DOM_LOAD_TIMEOUT_MS,
        )
    try:
        _run_actions_with_retries(
            open_page,
            "load page",
            max_attempts=max_navigation_attempts,
            retry_delay=FIRST_PAGE_LOAD_RETRY_DELAY_SECONDS,
            shutdown_requested=shutdown_requested,
        )
    except PageRestartRequired:
        _close_browser_quietly(browser)
        raise
    except Exception:
        logger.error(
            "Page load failed after %s attempts. Restarting script...",
            max_navigation_attempts,
        )
        _restart_bot(browser)
        raise

    logger.info("Page loaded, OK")
    return browser, page


def switch_to_live_table(page: Page) -> None:
    """Переключить страницу на фильтр Live."""
    live_filter = page.locator("li#li_FilterLive")
    live_table = page.locator("table#table_live")
    live_filter.wait_for(timeout=PAGE_ELEMENT_WAIT_TIMEOUT_MS)
    live_filter.click()
    live_table.wait_for(timeout=PAGE_ELEMENT_WAIT_TIMEOUT_MS)
    page.wait_for_timeout(HARDCODE_DELAY_MS)
    logger.info("Switched to Live")


def refresh_live_table(page: Page) -> None:
    """Обновить Live-таблицу кликом по фильтру."""
    logger.info("Refreshing...")
    try:
        live_filter = page.locator("li#li_FilterLive")
        live_table = page.locator("table#table_live")
        live_filter.wait_for(timeout=PAGE_ELEMENT_WAIT_TIMEOUT_MS)
        live_filter.click()
        live_table.wait_for(timeout=PAGE_ELEMENT_WAIT_TIMEOUT_MS)
        page.wait_for_timeout(HARDCODE_DELAY_MS)
        logger.info("Live table refreshed")
    except Exception as e:
        _capture_error_screenshot(page, "Failed to refresh Live table", e)
        raise


def refresh_page(page: Page) -> None:
    """Перезагрузить страницу и дождаться появления Live-таблицы."""
    logger.info("Refreshing...")
    try:
        page.reload(wait_until="domcontentloaded", timeout=PAGE_DOM_LOAD_TIMEOUT_MS)
        page.locator("table#table_live").wait_for(
            timeout=PAGE_ELEMENT_WAIT_TIMEOUT_MS
        )

        logger.info("Page refreshed")
    except Exception as e:
        _capture_error_screenshot(page, "Failed to refresh page", e)
        raise


def collect_crown_matches(page: Page) -> list[str]:
    """Собрать ID активных матчей, готовых к наблюдению."""
    try:
        page.wait_for_timeout(HARDCODE_DELAY_MS)
        match_status = collect_crown_matches_status(page)
        matches = list(dict.fromkeys(
            item['match_id'] for item in match_status if item['active']
        ))
    except Exception:
        logger.exception("Failed to collect matches")
        raise

    logger.info(f"Matches found: {len(matches)} ({', '.join(matches)})")
    if DEBUGMODE:
        write_match_count(match_status, matches)
    return matches


def _restart_bot(browser: Browser | None) -> NoReturn:
    """Закрыть браузер и перезапустить процесс."""
    if browser is not None:
        _close_browser_quietly(browser)
    os.execv(sys.executable, [sys.executable] + sys.argv)


def _check_for_restart(
    restart_deadline: float,
    browser: Browser | None,
    shutdown_requested: threading.Event | None = None,
) -> None:
    """Проверить, наступил ли срок планового перезапуска."""
    if shutdown_requested is not None and shutdown_requested.is_set():
        return
    if time.monotonic() < restart_deadline:
        return
    logger.info(
        "Restart interval reached (%s hours). Restarting script...",
        RESTART_HOURS,
    )
    if browser is None:
        _restart_bot(browser)
    raise ScheduledRestartRequired(
        f"Scheduled restart after {RESTART_HOURS} hours"
    )


def _validate_configuration(settings: Settings | None = None) -> None:
    """Проверить наличие обязательных настроек перед запуском."""
    runtime_settings = settings if settings is not None else config.load_settings()
    required_settings = {
        "BOT_TOKEN": runtime_settings.bot_token,
        "CHANNEL_ID": runtime_settings.channel_id,
        "BROWSER_PROXY_SERVER": runtime_settings.browser_proxy_server,
        "TELEGRAM_PROXY_HOST": runtime_settings.telegram_proxy_host,
        "TELEGRAM_PROXY_PORT": runtime_settings.telegram_proxy_port,
    }
    missing_settings: list[str] = []
    for name, value in required_settings.items():
        if not value.strip():
            missing_settings.append(name)
    if missing_settings:
        raise RuntimeError(
            "Missing required configuration: " + ", ".join(missing_settings)
        )


def main() -> None:
    """Основной цикл бота."""
    settings = config.load_settings()
    _validate_configuration(settings)
    leagues = config.load_leagues()
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
        _check_for_restart(restart_deadline, None, shutdown_requested)

        while not shutdown_requested.is_set():
            with sync_playwright() as p:
                try:
                    browser, page = init_browser(
                        p,
                        shutdown_requested=shutdown_requested,
                        settings=settings,
                    )
                except CookieSetupError as e:
                    logger.error("%s", e)
                    raise SystemExit(1) from None
                except PageRestartRequired:
                    if shutdown_requested.is_set():
                        logger.info("Shutdown requested during browser setup")
                        return
                    raise

                try:
                    _check_for_restart(restart_deadline, browser, shutdown_requested)
                    # Переключаемся на live-фильтр.
                    try:
                        _run_actions_with_retries(
                            lambda: switch_to_live_table(page),
                            "prepare page",
                            max_attempts=PREPARE_LIVE_TABLE_MAX_ATTEMPTS,
                            retry_delay=LIVE_TABLE_REFRESH_RETRY_DELAY_SECONDS,
                            shutdown_requested=shutdown_requested,
                        )
                    except Exception:
                        if shutdown_requested.is_set():
                            logger.info("Shutdown requested before Live setup finished")
                            break
                        logger.error(
                            "Failed to prepare page after %s attempts. "
                            "Restarting script...",
                            PREPARE_LIVE_TABLE_MAX_ATTEMPTS,
                        )
                        _restart_bot(browser)
                    if shutdown_requested.is_set():
                        break
                    _check_for_restart(restart_deadline, browser, shutdown_requested)
                    # Всегда получаем актуальные матчи; состояние загружается после ожидания,
                    # чтобы оно оставалось на диске, если перезапуск наступит без матчей.
                    matches = collect_crown_matches(page)
                    while not matches and not shutdown_requested.is_set():
                        _check_for_restart(restart_deadline, browser, shutdown_requested)
                        remaining_seconds = restart_deadline - time.monotonic()
                        logger.info(
                            "No matches found. Retrying in %s seconds...",
                            NO_MATCHES_RETRY_DELAY_SECONDS,
                        )
                        _wait_for_shutdown(
                            shutdown_requested,
                            max(
                                0,
                                min(NO_MATCHES_RETRY_DELAY_SECONDS, remaining_seconds),
                            ),
                        )
                        if shutdown_requested.is_set():
                            break
                        _check_for_restart(restart_deadline, browser, shutdown_requested)
                        # Обновляем страницу при отсутствии матчей.
                        try:
                            _run_actions_with_retries(
                                lambda: refresh_page(page),
                                "refresh page while waiting for matches",
                                max_attempts=PAGE_REFRESH_MAX_ATTEMPTS,
                                retry_delay=PAGE_REFRESH_RETRY_DELAY_SECONDS,
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
                                PAGE_REFRESH_MAX_ATTEMPTS,
                            )
                            _restart_bot(browser)
                        if shutdown_requested.is_set():
                            break
                        matches = collect_crown_matches(page)

                    if shutdown_requested.is_set():
                        break

                    _check_for_restart(restart_deadline, browser, shutdown_requested)
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
                        settings=settings,
                        leagues=leagues,
                    )
                except PageRestartRequired:
                    if shutdown_requested.is_set():
                        logger.info("Shutdown requested")
                        return
                    _restart_bot(browser)
                except Exception as e:
                    if shutdown_requested.is_set():
                        logger.info("Browser operation was interrupted during shutdown")
                        return
                    logger.error(f"Unexpected error in main: {e}")
                    _close_browser_quietly(browser)
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