import os
import re
from datetime import datetime

from playwright.sync_api import Page

from app.config import (
    DEBUGMODE,
    ERROR_SCREENSHOTS_DIR,
    HARDCODE_DELAY_MS,
    PAGE_DOM_LOAD_TIMEOUT_MS,
    PAGE_ELEMENT_WAIT_TIMEOUT_MS,
)
from app.logger import setup_logger
from app.monitoring.observer import (
    collect_crown_matches_status,
    page_is_closed,
    write_match_count,
)

logger = setup_logger(__name__)


def _capture_error_screenshot(
    page: Page, error_label: str, error: Exception
) -> None:
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
    except Exception as error:
        _capture_error_screenshot(page, "Failed to refresh Live table", error)
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
    except Exception as error:
        _capture_error_screenshot(page, "Failed to refresh page", error)
        raise


def collect_crown_matches(page: Page) -> list[str]:
    """Собрать ID активных матчей, готовых к наблюдению."""
    try:
        page.wait_for_timeout(HARDCODE_DELAY_MS)
        match_status = collect_crown_matches_status(page)
        matches = list(
            dict.fromkeys(
                item["match_id"] for item in match_status if item["active"]
            )
        )
    except Exception:
        logger.exception("Failed to collect matches")
        raise

    logger.info("Matches found: %s (%s)", len(matches), ", ".join(matches))
    if DEBUGMODE:
        write_match_count(match_status, matches)
    return matches
