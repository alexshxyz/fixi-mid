import threading
import time
from collections.abc import Callable
from contextlib import suppress
from typing import NoReturn

from playwright.sync_api import Browser, Page, Playwright

from app import config
from app.config import (
    BROWSER_HEADLESS,
    FIRST_PAGE_LOAD_RETRY_DELAY_SECONDS,
    PAGE_DOM_LOAD_MAX_ATTEMPTS,
    PAGE_DOM_LOAD_TIMEOUT_MS,
    SITE_COOKIES,
    SITE_URL,
    Settings,
)
from app.logger import setup_logger
from app.monitoring.observer import PageRestartRequired, raise_if_shutdown

logger = setup_logger(__name__)


def _wait_for_shutdown(
    shutdown_requested: threading.Event, seconds: float
) -> bool:
    """Ожидать завершения работы или истечения указанного времени."""
    return shutdown_requested.wait(seconds)


class CookieSetupError(RuntimeError):
    """Сообщить о невозможности установить обязательные cookies."""


def close_browser_quietly(browser: Browser) -> None:
    """Закрыть браузер, не скрывая исходную ошибку ошибкой закрытия."""
    with suppress(Exception):
        browser.close()


def run_actions_with_retries(
    action: Callable[[], object],
    action_name: str,
    max_attempts: int,
    retry_delay: float,
    shutdown_requested: threading.Event | None = None,
) -> object | None:
    """Повторять действие, прерывая ожидание при запросе завершения."""
    for attempt in range(1, max_attempts + 1):
        raise_if_shutdown(shutdown_requested, action_name)
        try:
            result = action()
            raise_if_shutdown(shutdown_requested, action_name)
            return result
        except PageRestartRequired:
            raise
        except Exception as error:
            raise_if_shutdown(shutdown_requested, action_name, error)
            if attempt == 1 and not getattr(error, "_fixi_error_logged", False):
                logger.error("Failed to %s: %s", action_name, error)
                setattr(error, "_fixi_error_logged", True)
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
            raise_if_shutdown(shutdown_requested, action_name, error)
    return None


def init_browser(
    p: Playwright,
    max_navigation_attempts: int = PAGE_DOM_LOAD_MAX_ATTEMPTS,
    shutdown_requested: threading.Event | None = None,
    *,
    settings: Settings | None = None,
    restart_on_navigation_failure: Callable[[Browser | None], NoReturn] | None = None,
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
    except Exception as error:
        raise_if_shutdown(shutdown_requested, "browser launch", error)
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
        except Exception as error:
            raise CookieSetupError(
                f"Failed to add required site cookies: {error}"
            ) from error
        raise_if_shutdown(shutdown_requested, "browser setup")
    except PageRestartRequired:
        close_browser_quietly(browser)
        raise
    except Exception:
        close_browser_quietly(browser)
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
        run_actions_with_retries(
            open_page,
            "load page",
            max_attempts=max_navigation_attempts,
            retry_delay=FIRST_PAGE_LOAD_RETRY_DELAY_SECONDS,
            shutdown_requested=shutdown_requested,
        )
    except PageRestartRequired:
        close_browser_quietly(browser)
        raise
    except Exception:
        logger.error(
            "Page load failed after %s attempts. Restarting script...",
            max_navigation_attempts,
        )
        if restart_on_navigation_failure is None:
            close_browser_quietly(browser)
        else:
            restart_on_navigation_failure(browser)
        raise

    logger.info("Page loaded, OK")
    return browser, page
