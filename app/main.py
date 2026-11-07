import os
import signal
import sys
import threading
import time
from functools import partial
from types import FrameType
from typing import NoReturn

if __package__ in (None, ""):
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    sys.path.insert(0, project_root)

from app import config
from playwright.sync_api import Browser, Page, Playwright, sync_playwright
from app.browser import session as browser_session
from app.browser.livepage import (
    collect_crown_matches,
    refresh_live_table,
    refresh_page,
    switch_to_live_table,
)
from app.browser.session import (
    CookieSetupError,
    close_browser_quietly,
    run_actions_with_retries,
)
from app.monitoring.tracker import parse_and_monitor_match
from app.persistence.state import load_state_from_json
from app.persistence.storage import init_storage
from app.config import (
    LIVE_TABLE_REFRESH_RETRY_DELAY_SECONDS,
    NO_MATCHES_RETRY_DELAY_SECONDS,
    PAGE_DOM_LOAD_MAX_ATTEMPTS,
    PAGE_REFRESH_MAX_ATTEMPTS,
    PAGE_REFRESH_RETRY_DELAY_SECONDS,
    PREPARE_LIVE_TABLE_MAX_ATTEMPTS,
    RESTART_HOURS,
    STATE_SAVE_FILE,
    Settings,
)
from app.logger import setup_logger
from app.monitoring.observer import (
    PageRestartRequired,
    ScheduledRestartRequired,
)

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


def init_browser(
    p: Playwright,
    max_navigation_attempts: int = PAGE_DOM_LOAD_MAX_ATTEMPTS,
    shutdown_requested: threading.Event | None = None,
    *,
    settings: Settings | None = None,
) -> tuple[Browser, Page]:
    """Инициализировать браузер; решение о перезапуске процесса остаётся здесь."""
    return browser_session.init_browser(
        p,
        max_navigation_attempts=max_navigation_attempts,
        shutdown_requested=shutdown_requested,
        settings=settings,
        restart_on_navigation_failure=_restart_bot,
    )


def _restart_bot(browser: Browser | None) -> NoReturn:
    """Закрыть браузер и перезапустить процесс."""
    if browser is not None:
        close_browser_quietly(browser)
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
                        run_actions_with_retries(
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
                            run_actions_with_retries(
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
                    close_browser_quietly(browser)
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