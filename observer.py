import json
import os
import time
from datetime import datetime

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from config import (
    DEBUGMODE,
    HARD_PAGE_REFRESH_MAX_RETRIES,
    HARD_PAGE_REFRESH_RETRY_DELAY_SECONDS,
    LIVE_CHANGE_POLL_INTERVAL_MS,
    LIVE_REFRESH_MAX_RETRIES,
    LIVE_REFRESH_RETRY_DELAY_SECONDS,
)
from logger import setup_logger
from scripts import (
    COLLECT_MATCH_STATUS,
    EXTRACT_ALL_MATCH_DATA,
    INSTALL_LIVE_CHANGE_OBSERVER,
    LIVE_CHANGE_PREDICATE,
    LIVE_ODDS_READINESS,
    REMOVE_LIVE_CHANGE_OBSERVER,
    RESET_LIVE_CHANGE_FLAG,
)

logger = setup_logger('tracker')


class PageRestartRequired(Exception):
    pass


# Устанавливает наблюдатель за изменениями Live-таблицы в DOM.
def _install_live_change_observer(page):
    page.evaluate(INSTALL_LIVE_CHANGE_OBSERVER)


# Отключает наблюдатель за Live-таблицей и удаляет его флаги из страницы.
def _remove_live_change_observer(page):
    try:
        page.evaluate(REMOVE_LIVE_CHANGE_OBSERVER)
    except Exception:
        logger.warning("Could not stop live table observer.", exc_info=True)


# Ждёт изменения Live-таблицы и возвращает, произошло ли оно до таймаута.
def _wait_for_live_change(page, timeout_ms):
    try:
        page.wait_for_function(
            LIVE_CHANGE_PREDICATE,
            timeout=timeout_ms,
            polling=LIVE_CHANGE_POLL_INTERVAL_MS,
        )
    except PlaywrightTimeoutError:
        return False

    page.evaluate(RESET_LIVE_CHANGE_FLAG)
    return True


# Обновляет Live-таблицу с ограниченным числом повторных попыток.
def _reload_page_with_retries(
    page,
    live_refresh_callback,
    max_retries=LIVE_REFRESH_MAX_RETRIES,
    retry_delay=LIVE_REFRESH_RETRY_DELAY_SECONDS,
):
    for attempt in range(1, max_retries + 1):
        try:
            live_refresh_callback(page)
            match_status = _collect_match_status(page, require_crown=True)
            if not any(item['active'] for item in match_status):
                return False

            data_ready = page.evaluate(LIVE_ODDS_READINESS)

            if not data_ready["hasCrownOdds"] or not data_ready["hasVisibleOdds"]:
                return False

            return True
        except Exception as e:
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
            time.sleep(retry_delay)
    return None


# Собирает видимые строки Live и отмечает, проходят ли они критерии наблюдения.
def _collect_match_status(page, require_crown=False):
    return page.evaluate(COLLECT_MATCH_STATUS, require_crown)


# Возвращает ID матчей, которые сейчас видны и проходят критерии наблюдения.
def _collect_match_ids(page):
    return [
        item['match_id']
        for item in _collect_match_status(page)
        if item['active']
    ]


# Перезаписывает диагностический список активных и исключённых матчей.
def _write_match_count(match_status, active_match_ids):
    if DEBUGMODE != 1:
        return

    active_ids = {str(match_id) for match_id in active_match_ids}
    payload = {'Active': [], 'Inactive': []}
    for item in match_status:
        category = 'Active' if str(item['match_id']) in active_ids else 'Inactive'
        payload[category].append(item['label'])

    snapshot_path = os.path.join(os.path.dirname(__file__), 'count.json')
    try:
        with open(snapshot_path, 'w', encoding='utf-8') as snapshot_file:
            json.dump(payload, snapshot_file, ensure_ascii=False, indent=2)
            snapshot_file.write('\n')
    except OSError:
        logger.exception("Failed to write match count snapshot to %s", snapshot_path)


# Извлекает данные ВСЕ матчей за один evaluate() вызов.
def _extract_all_match_data(page, match_ids):
    try:
        match_data = page.evaluate(EXTRACT_ALL_MATCH_DATA, match_ids)
    except Exception as e:
        logger.error(f"Error in _extract_all_match_data: {e}")
        raise

    observed_at = datetime.now().astimezone().isoformat(timespec="seconds")
    for entry in match_data.values():
        if entry is not None:
            entry["date"] = observed_at

    return match_data