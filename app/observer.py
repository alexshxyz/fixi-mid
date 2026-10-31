import json
import time
from datetime import datetime
from threading import Event
from typing import Any
from collections.abc import Callable, Sequence

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from .config import (
    DATA_DIR,
    DEBUGMODE,
    LIVE_CHANGE_POLL_INTERVAL_MS,
    LIVE_TABLE_REFRESH_MAX_ATTEMPTS,
    LIVE_TABLE_REFRESH_RETRY_DELAY_SECONDS,
)
from .logger import setup_logger
from .match_types import MatchSnapshot
from .scripts import (
    COLLECT_MATCH_STATUS,
    EXTRACT_ALL_MATCH_DATA,
    INSTALL_LIVE_CHANGE_OBSERVER,
    LIVE_CHANGE_PREDICATE,
    REMOVE_LIVE_CHANGE_OBSERVER,
    RESET_LIVE_CHANGE_FLAG,
)

logger = setup_logger(__name__)


def page_is_closed(page: Page, error: Exception | None = None) -> bool:
    """Проверить, закрыта ли страница браузера."""
    is_closed = getattr(page, "is_closed", None)
    page_closed_flag = callable(is_closed) and is_closed()
    if isinstance(error, PlaywrightError) and page_closed_flag:
        return True

    if error is not None and "has been closed" in str(error).lower():
        return True

    return page_closed_flag


class PageRestartRequired(Exception):
    """Сообщить, что мониторинг должен перезапустить страницу или процесс."""

    pass


class ScheduledRestartRequired(PageRestartRequired):
    """Сообщить о наступлении планового перезапуска."""

    pass


def raise_if_shutdown(
    shutdown_requested: Event | None,
    where: str | None = None,
    cause: Exception | None = None,
) -> None:
    """Прервать операцию, если запрошено завершение."""
    if shutdown_requested is not None and shutdown_requested.is_set():
        message = (
            f"Shutdown requested during {where}"
            if where is not None
            else "Shutdown requested"
        )
        error = PageRestartRequired(message)
        if cause is not None:
            raise error from cause
        raise error


def install_live_change_observer(page: Page) -> None:
    """Установить наблюдатель за изменениями Live-таблицы в DOM."""
    page.evaluate(INSTALL_LIVE_CHANGE_OBSERVER)


def remove_live_change_observer(page: Page) -> None:
    """Отключить наблюдатель и удалить его флаги из страницы."""
    if page_is_closed(page):
        return
    try:
        page.evaluate(REMOVE_LIVE_CHANGE_OBSERVER)
    except Exception:
        logger.warning("Could not stop live table observer.", exc_info=True)


def wait_for_live_change(
    page: Page,
    timeout_ms: int,
    shutdown_requested: Event | None = None,
) -> bool:
    """Дождаться изменения Live-таблицы или истечения таймаута."""
    if shutdown_requested is None:
        try:
            page.wait_for_function(
                LIVE_CHANGE_PREDICATE,
                timeout=timeout_ms,
                polling=LIVE_CHANGE_POLL_INTERVAL_MS,
            )
        except PlaywrightTimeoutError:
            return False
    else:
        deadline = time.monotonic() + max(1, timeout_ms) / 1000
        while not shutdown_requested.is_set():
            remaining_ms = int((deadline - time.monotonic()) * 1000)
            if remaining_ms <= 0:
                return False
            try:
                page.wait_for_function(
                    LIVE_CHANGE_PREDICATE,
                    timeout=min(remaining_ms, 1000),
                    polling=LIVE_CHANGE_POLL_INTERVAL_MS,
                )
                break
            except PlaywrightTimeoutError:
                continue
        if shutdown_requested.is_set():
            return False

    page.evaluate(RESET_LIVE_CHANGE_FLAG)
    return True


def reload_page_with_retries(
    page: Page,
    live_refresh_callback: Callable[[Page], None],
    max_retries: int = LIVE_TABLE_REFRESH_MAX_ATTEMPTS,
    retry_delay: float = LIVE_TABLE_REFRESH_RETRY_DELAY_SECONDS,
    shutdown_requested: Event | None = None,
) -> bool | None:
    """Обновить Live-таблицу, повторяя неудачные попытки."""
    for attempt in range(1, max_retries + 1):
        if shutdown_requested is not None and shutdown_requested.is_set():
            raise PageRestartRequired("Shutdown requested during Live refresh")

        try:
            live_refresh_callback(page)
            match_status = collect_match_status(page, require_crown=True)
            if shutdown_requested is not None and shutdown_requested.is_set():
                raise PageRestartRequired(
                    "Shutdown requested during Live refresh"
                )
            return any(item['active'] for item in match_status)
        except PageRestartRequired:
            raise
        except Exception as e:
            if shutdown_requested is not None and shutdown_requested.is_set():
                raise PageRestartRequired(
                    "Shutdown requested during Live refresh"
                ) from e

            if attempt == max_retries:
                logger.error(
                    "Live refresh failed after %s attempts: %s",
                    max_retries,
                    e,
                )
                return None

            logger.warning(
                "Live refresh attempt %s/%s failed: %s. Retrying in %s seconds...",
                attempt,
                max_retries,
                e,
                retry_delay,
            )
            if shutdown_requested is None:
                time.sleep(retry_delay)
            elif shutdown_requested.wait(retry_delay):
                raise PageRestartRequired(
                    "Shutdown requested during Live refresh"
                ) from e
    return None


def collect_match_status(
    page: Page,
    require_crown: bool = False,
) -> list[dict[str, Any]]:
    """Собрать статусы видимых матчей в Live-таблице."""
    match_status: list[dict[str, Any]] = page.evaluate(
        COLLECT_MATCH_STATUS, require_crown
    )
    return match_status


def write_match_count(
    match_status: list[dict[str, Any]],
    active_match_ids: Sequence[str | int],
) -> None:
    """Записать диагностический список активных и исключённых матчей."""
    if not DEBUGMODE:
        return

    active_ids = {str(match_id) for match_id in active_match_ids}
    payload: dict[str, list[str]] = {'Active': [], 'Inactive': []}
    for item in match_status:
        category = 'Active' if str(item['match_id']) in active_ids else 'Inactive'
        payload[category].append(item['label'])

    snapshot_path = DATA_DIR / 'count.json'
    try:
        snapshot_path.parent.mkdir(parents=True, exist_ok=True)
        with open(snapshot_path, 'w', encoding='utf-8') as snapshot_file:
            json.dump(payload, snapshot_file, ensure_ascii=False, indent=2)
            snapshot_file.write('\n')
    except OSError:
        logger.exception("Failed to write match count snapshot to %s", snapshot_path)


def extract_all_match_data(
    page: Page,
    match_ids: list[str],
) -> dict[str, MatchSnapshot | None]:
    """Извлечь данные указанных матчей одним вызовом evaluate()."""
    try:
        match_data: dict[str, MatchSnapshot | None] = page.evaluate(
            EXTRACT_ALL_MATCH_DATA, match_ids
        )
    except Exception as e:
        logger.error(f"Error in extract_all_match_data: {e}")
        raise

    observed_at = datetime.now().astimezone().isoformat(timespec="seconds")
    for entry in match_data.values():
        if entry is not None:
            entry["date"] = observed_at

    return match_data


__all__ = [
    "PageRestartRequired",
    "ScheduledRestartRequired",
    "page_is_closed",
    "install_live_change_observer",
    "remove_live_change_observer",
    "wait_for_live_change",
    "reload_page_with_retries",
    "collect_match_status",
    "write_match_count",
    "extract_all_match_data",
]