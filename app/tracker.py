import json
import time
from collections.abc import Callable
from pathlib import Path
from threading import Event
from typing import Any, NoReturn

from playwright.sync_api import Page

from . import pending_notifications, state
from .closed_ov_ah import find_pattern_matches
from .config import (
    DATA_DIR,
    DEBUGMODE,
    PAGE_REFRESH_MAX_ATTEMPTS,
    PAGE_REFRESH_RETRY_DELAY_SECONDS,
    HEARTBEAT_INTERVAL_SECONDS,
    NO_MATCHES_RETRY_DELAY_SECONDS,
    PAGE_LIVE_RELOAD,
    RESTART_HOURS,
    STATE_SAVE_FILE,
    get_table_live_reload_interval,
)
from .logger import setup_logger
from .match_types import MatchHistoryMap, MatchSnapshot, NotificationSentDetails
from .observer import (
    PageRestartRequired,
    ScheduledRestartRequired,
    raise_if_shutdown,
    collect_match_status,
    extract_all_match_data,
    install_live_change_observer,
    page_is_closed,
    reload_page_with_retries,
    remove_live_change_observer,
    wait_for_live_change,
    write_match_count,
)

logger = setup_logger(__name__)


class MatchMonitor:
    """Мониторить состояние и коэффициенты активных матчей."""

    def __init__(
        self,
        page: Page,
        match_ids: list[str] | None = None,
        saved_state: dict[str, Any] | None = None,
        *,
        live_refresh_callback: Callable[[Page], None],
        page_refresh_callback: Callable[[Page], None] | None = None,
        restart_deadline: float | None = None,
        shutdown_requested: Event | None = None,
        saved_state_path: str | Path | None = None,
    ) -> None:
        """Инициализировать монитор, его состояние и расписание проверок."""
        self.page: Page = page
        self.match_ids: list[str] | None = match_ids
        self.saved_state: dict[str, Any] | None = saved_state
        self.live_refresh_callback: Callable[[Page], None] = live_refresh_callback
        self.page_refresh_callback: Callable[[Page], None] | None = (
            page_refresh_callback
        )
        self.shutdown_requested: Event | None = shutdown_requested
        self.saved_state_path = saved_state_path
        self.match_history: MatchHistoryMap = {}
        self.last_data: dict[str, MatchSnapshot] = {}
        self.pending_notifications: dict[str, dict[str, Any]] = {}
        self.active_match_ids: list[str] = []
        self.next_reload_at: float = time.monotonic() + get_table_live_reload_interval()
        self.next_page_refresh_at: float = time.monotonic() + PAGE_LIVE_RELOAD
        self.next_heartbeat_at: float = time.monotonic() + HEARTBEAT_INTERVAL_SECONDS
        self.restart_deadline: float = (
            restart_deadline
            if restart_deadline is not None
            else time.monotonic() + RESTART_HOURS * 3600
        )

    def run(self) -> None:
        """Запустить цикл мониторинга и сохранить состояние при завершении."""
        if self._shutdown_is_requested():
            return
        try:
            self._init_or_restore_state()
            if self._shutdown_is_requested():
                return
            logger.info("Monitoring started")
            install_live_change_observer(self.page)
            try:
                self._monitor_loop()
            finally:
                remove_live_change_observer(self.page)
        except PageRestartRequired:
            raise
        except Exception as e:
            if self._shutdown_is_requested():
                if page_is_closed(self.page, e):
                    logger.info("Browser page was closed during shutdown")
                else:
                    logger.warning(
                        "Browser operation was interrupted during shutdown",
                        exc_info=True,
                    )
                return
            logger.exception("Error in parse_and_monitor_match")
            if not self._save_state_to_json(self.active_match_ids, self.last_data):
                logger.error("Could not save monitoring state before restart")
            raise PageRestartRequired(
                f"Monitoring failed; restarting after error: {e}"
            ) from e
        finally:
            if self._shutdown_is_requested() and not self._save_state_to_json(
                self.active_match_ids, self.last_data
            ):
                logger.error("Could not save monitoring state before shutdown")

    def _shutdown_is_requested(self) -> bool:
        """Проверить, запрошено ли завершение мониторинга."""
        return (
            self.shutdown_requested is not None
            and self.shutdown_requested.is_set()
        )

    def _raise_if_shutdown(self, cause: Exception | None = None) -> None:
        """Прервать обновление страницы, если запрошено завершение."""
        raise_if_shutdown(self.shutdown_requested, cause=cause)

    def _save_state_to_json(
        self,
        active_match_ids: list[str],
        last_data: dict[str, MatchSnapshot],
        path: str | Path = STATE_SAVE_FILE,
    ) -> bool:
        """Сохранить текущее состояние матчей в JSON-файл."""
        return state.save_state_to_json(
            self.match_history,
            last_data,
            active_match_ids,
            self.pending_notifications,
            path,
        )

    def _init_or_restore_state(self) -> None:
        """Восстановить состояние из файла или инициализировать его."""
        restored_state = state.restore_state(self.saved_state)
        if restored_state is not None:
            self.match_history.update(restored_state["match_history"])
            self.last_data = restored_state["last_data"]
            self.pending_notifications = restored_state["pending_notifications"]
            self.active_match_ids = restored_state["active_match_ids"]
            if self.match_ids is not None:
                self._synchronize_matches(self.match_ids)
            if self.saved_state_path is not None:
                state.remove_state_file(self.saved_state_path)
            return

        self.active_match_ids = list(self.match_ids or [])
        if self.active_match_ids:
            self._load_initial_data()

        self._run_analyzer()

    def _run_analyzer(self) -> None:
        """Подготовить историю и передать её анализатору рынков."""
        if self._shutdown_is_requested():
            return
        if DEBUGMODE:
            snapshot_path = DATA_DIR / "data_closed_ov_ah.json"
            try:
                snapshot_path.parent.mkdir(parents=True, exist_ok=True)
                with open(snapshot_path, "w", encoding="utf-8") as snapshot_file:
                    json.dump(
                        self.match_history,
                        snapshot_file,
                        ensure_ascii=False,
                        indent=2,
                    )
                    snapshot_file.write("\n")
            except OSError:
                logger.exception("Failed to write analyzer input snapshot to %s", snapshot_path)

        find_pattern_matches(
            self.match_history,
            self._register_pending_notification,
            shutdown_requested=self.shutdown_requested,
        )

    def _register_pending_notification(
        self, notification: NotificationSentDetails
    ) -> None:
        """Запланировать отложенную проверку отправленного уведомления."""
        pending_notifications.register_pending_notification(
            self.pending_notifications, notification
        )

    def _process_due_notifications(self) -> None:
        """Обработать истёкшие таймеры и обновить Telegram-сообщения."""
        pending_notifications.process_due_notifications(
            self.pending_notifications,
            self.last_data,
            self.active_match_ids,
            shutdown_requested=self.shutdown_requested,
        )

    def _next_notification_timeout(self) -> float | None:
        """Вычислить время ожидания до ближайшей проверки уведомления."""
        return pending_notifications.next_notification_timeout(
            self.pending_notifications
        )

    def _load_initial_data(self) -> None:
        """Загрузить исходный снимок каждого активного матча."""
        initial_all_data = extract_all_match_data(self.page, self.active_match_ids)

        for match_id in self.active_match_ids:
            initial_data = initial_all_data.get(match_id)
            if initial_data:
                self.match_history[match_id] = {'initial': initial_data, 'changes': []}
                self.last_data[match_id] = state.get_last_data(initial_data)
            else:
                logger.info(f"No initial data for match {match_id}")

    def _monitor_loop(self) -> None:
        """Выполнять проверки уведомлений и обновления матчей по расписанию."""
        while not self._shutdown_is_requested():
            self._process_due_notifications()
            self._restart_if_due()
            if self._shutdown_is_requested():
                break

            now = time.monotonic()
            if now >= self.next_heartbeat_at:
                self._tick_heartbeat(now)

            handled = self._tick_page_refresh(now)
            if not handled:
                handled = self._tick_table_reload(now)
            if not handled:
                self._tick_live_change(now)
            self._restart_if_due()

    def _tick_heartbeat(self, now: float) -> None:
        """Обновить heartbeat, если наступил срок."""
        logger.info("Heartbeat: OK")
        self.next_heartbeat_at = now + HEARTBEAT_INTERVAL_SECONDS

    def _tick_page_refresh(self, now: float) -> bool:
        """Выполнить плановую полную перезагрузку страницы при необходимости."""
        if not self.page_refresh_callback or now < self.next_page_refresh_at:
            return False

        try:
            data_changed = self._do_scheduled_page_refresh()
            self.next_reload_at = (
                time.monotonic() + get_table_live_reload_interval()
            )
            self.next_page_refresh_at = time.monotonic() + PAGE_LIVE_RELOAD
        except PageRestartRequired:
            raise
        except Exception as e:
            logger.error("Scheduled page refresh failed: %s", e)
            self.next_page_refresh_at = (
                time.monotonic() + PAGE_REFRESH_RETRY_DELAY_SECONDS
            )
            data_changed = False

        if data_changed:
            self._run_analyzer()
        if not self._shutdown_is_requested():
            self._process_due_notifications()
        return True

    def _tick_table_reload(self, now: float) -> bool:
        """Выполнить очередное обновление таблицы и матчевых данных."""
        if now < self.next_reload_at:
            return False

        data_changed = self._do_periodic_reload()
        if self._shutdown_is_requested():
            return True
        if self.active_match_ids:
            data_changed = (
                self._poll_and_update(log_data_loaded=True)
                or data_changed
            )
        if data_changed:
            self._run_analyzer()
        if not self._shutdown_is_requested():
            self._process_due_notifications()
        return True

    def _tick_live_change(self, now: float) -> None:
        """Ожидать изменения Live-таблицы и обработать новые данные."""
        wait_seconds = min(
            self.next_reload_at - now,
            self.next_page_refresh_at - now
            if self.page_refresh_callback
            else float("inf"),
            self.restart_deadline - now,
            self.next_heartbeat_at - now,
        )
        notification_timeout = self._next_notification_timeout()
        if notification_timeout is not None:
            wait_seconds = min(wait_seconds, notification_timeout)
        changed = wait_for_live_change(
            self.page,
            timeout_ms=max(1, int(wait_seconds * 1000)),
            shutdown_requested=self.shutdown_requested,
        )
        if self._shutdown_is_requested():
            return

        data_changed = (
            self._poll_and_update()
            if changed and self.active_match_ids
            else False
        )
        if data_changed:
            self._run_analyzer()
        self._process_due_notifications()

    def _restart_if_due(self) -> None:
        """Проверить срок перезапуска и запустить его при необходимости."""
        if self._shutdown_is_requested():
            return
        if time.monotonic() >= self.restart_deadline:
            self._trigger_scheduled_restart()

    def _trigger_scheduled_restart(self) -> NoReturn:
        """Сохранить текущее состояние и запросить плановый перезапуск."""
        logger.info(
            "Restart interval reached (%s hours). Restarting script...",
            RESTART_HOURS,
        )
        if self._save_state_to_json(self.active_match_ids, self.last_data):
            logger.info("State saved successfully before scheduled restart.")
        else:
            logger.error("Failed to save state, but proceeding with restart.")
        raise ScheduledRestartRequired(
            f"Scheduled restart after {RESTART_HOURS} hours"
        )

    def _do_periodic_reload(self) -> bool:
        """Обновить Live-таблицу и синхронизировать активные матчи."""
        live_refresh_succeeded = reload_page_with_retries(
            self.page,
            self.live_refresh_callback,
            shutdown_requested=self.shutdown_requested,
        )
        self._raise_if_shutdown()
        if live_refresh_succeeded is False:
            match_status = collect_match_status(self.page, require_crown=True)
        elif live_refresh_succeeded is None:
            logger.error(
                "Live refresh failed after retries. Saving state and restarting script."
            )
            if not self._save_state_to_json(self.active_match_ids, self.last_data):
                logger.error("Could not save monitoring state before restart")
            raise PageRestartRequired(
                "Live refresh failed after retries; restarting script"
            )
        else:
            match_status = collect_match_status(self.page, require_crown=True)

        self._raise_if_shutdown()
        if not any(item['active'] for item in match_status):
            match_status = self._wait_for_matches_with_hard_refresh(match_status)

        current_match_ids = [
            item['match_id'] for item in match_status if item['active']
        ]
        synchronized = self._synchronize_matches(current_match_ids)
        write_match_count(match_status, self.active_match_ids)
        self.next_reload_at = time.monotonic() + get_table_live_reload_interval()
        return synchronized

    def _wait_for_matches_with_hard_refresh(
        self,
        match_status: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        """Ожидать появления матчей, периодически перезагружая страницу."""
        if match_status is None:
            match_status = collect_match_status(self.page, require_crown=True)

        while not any(item['active'] for item in match_status):
            self._restart_if_due()
            logger.info(
                "No matches found. Retrying in %s seconds...",
                NO_MATCHES_RETRY_DELAY_SECONDS,
            )
            self._wait_for_shutdown_or_timeout(
                max(
                    0,
                    min(
                        NO_MATCHES_RETRY_DELAY_SECONDS,
                        self.restart_deadline - time.monotonic(),
                    ),
                )
            )
            if self._shutdown_is_requested():
                raise PageRestartRequired("Shutdown requested")
            self._restart_if_due()
            match_status = self._do_hard_page_refresh_with_retries()
            self._restart_if_due()
        return match_status

    def _do_scheduled_page_refresh(self) -> bool:
        """Перезагрузить страницу и обновить данные активных матчей."""
        match_status = self._do_hard_page_refresh_with_retries()
        match_status = self._wait_for_matches_with_hard_refresh(match_status)
        self._raise_if_shutdown()
        current_match_ids = [
            item['match_id'] for item in match_status if item['active']
        ]
        data_changed = self._synchronize_matches(current_match_ids)
        self._raise_if_shutdown()
        write_match_count(match_status, self.active_match_ids)
        if self.active_match_ids:
            data_changed = (
                self._poll_and_update(log_data_loaded=True)
                or data_changed
            )
        return data_changed

    def _do_hard_page_refresh_with_retries(
        self,
        max_retries: int = PAGE_REFRESH_MAX_ATTEMPTS,
        retry_delay: float = PAGE_REFRESH_RETRY_DELAY_SECONDS,
    ) -> list[dict[str, Any]]:
        """Повторять полную перезагрузку страницы с сохранением состояния."""
        if max_retries < 1:
            raise ValueError("max_retries must be at least 1")

        for attempt in range(1, max_retries + 1):
            self._raise_if_shutdown()
            self._restart_if_due()
            try:
                if self.page_refresh_callback is None:
                    raise RuntimeError("Page refresh callback is not configured")
                self.page_refresh_callback(self.page)
                self._raise_if_shutdown()
                self._restart_if_due()
                install_live_change_observer(self.page)
                match_status = collect_match_status(self.page, require_crown=True)
                self._raise_if_shutdown()
                self._restart_if_due()
                return match_status
            except PageRestartRequired:
                raise
            except Exception as e:
                if self._shutdown_is_requested():
                    logger.info("Shutdown requested during page refresh")
                    self._raise_if_shutdown(e)

                page_closed = page_is_closed(self.page, e)
                if attempt < max_retries and not page_closed:
                    logger.warning(
                        "Page refresh attempt %s/%s failed: %s. "
                        "Retrying in %s seconds...",
                        attempt,
                        max_retries,
                        e,
                        retry_delay,
                    )
                    self._wait_for_shutdown_or_timeout(
                        max(
                            0,
                            min(
                                retry_delay,
                                self.restart_deadline - time.monotonic(),
                            ),
                        )
                    )
                    self._raise_if_shutdown(e)
                    self._restart_if_due()
                    continue

                if self._save_state_to_json(self.active_match_ids, self.last_data):
                    logger.error(
                        "Page refresh failed after %s attempts. "
                        "Saved state to %s and requesting restart.",
                        attempt,
                        STATE_SAVE_FILE,
                    )
                else:
                    logger.error(
                        "Page refresh failed after %s attempts and state save "
                        "failed. Requesting restart anyway.",
                        attempt,
                    )
                raise PageRestartRequired(
                    f"Page refresh failed after {attempt} attempts: {e}"
                ) from e
        raise PageRestartRequired("Page refresh retry loop ended without a result")

    def _wait_for_shutdown_or_timeout(self, seconds: float) -> None:
        """Подождать заданное время или сигнал завершения."""
        if self.shutdown_requested is not None:
            self.shutdown_requested.wait(seconds)
        else:
            time.sleep(seconds)

    def _synchronize_matches(self, current_match_ids: list[str]) -> bool:
        """Синхронизировать текущий список активных матчей."""
        self._raise_if_shutdown()
        current_match_ids = list(dict.fromkeys(current_match_ids))
        previous_match_ids = set(self.active_match_ids)
        current_match_id_set = set(current_match_ids)
        new_match_ids = [match_id for match_id in current_match_ids if match_id not in previous_match_ids]
        removed_match_ids = [match_id for match_id in self.active_match_ids if match_id not in current_match_id_set]

        for removed_id in removed_match_ids:
            self.match_history.pop(removed_id, None)
            for message_id, notification in list(
                self.pending_notifications.items()
            ):
                if str(notification['match_id']) == str(removed_id):
                    del self.pending_notifications[message_id]
            self.last_data.pop(removed_id, None)

        initialized_match_ids = [match_id for match_id in current_match_ids if match_id in previous_match_ids]
        if new_match_ids:
            new_data = extract_all_match_data(self.page, new_match_ids)

            for new_id in new_match_ids:
                self._raise_if_shutdown()
                initial_data = new_data.get(new_id)
                if initial_data:
                    self.match_history[new_id] = {'initial': initial_data, 'changes': []}
                    self.last_data[new_id] = state.get_last_data(initial_data)
                    initialized_match_ids.append(new_id)

        self.active_match_ids = initialized_match_ids
        logger.info(
            "Matches synchronized: Active %d (%s) New %d (%s) Removed %d (%s)",
            len(self.active_match_ids),
            ", ".join(self.active_match_ids) or "-",
            len(new_match_ids),
            ", ".join(new_match_ids) or "-",
            len(removed_match_ids),
            ", ".join(removed_match_ids) or "-",
        )
        return bool(new_match_ids or removed_match_ids)

    def _poll_and_update(self, log_data_loaded: bool = False) -> bool:
        """Получить актуальные данные матчей и обновить их историю."""
        all_match_data = extract_all_match_data(self.page, self.active_match_ids)
        self._raise_if_shutdown()

        if log_data_loaded:
            logger.info("Matches data reloaded")

        updated_match_ids = []
        analysis_data_changed = False
        for match_id in self.active_match_ids:
            self._raise_if_shutdown()
            if match_id not in self.last_data:
                continue
            current_data = all_match_data.get(match_id)
            if not current_data:
                continue
            last_match_data = self.last_data[match_id]
            existing_data_changed = (
                current_data['ah'] != last_match_data['ah'] or
                current_data['ov'] != last_match_data['ov'] or
                current_data.get('match_time', 'Unknown') !=
                last_match_data.get('match_time', 'Unknown') or
                current_data.get('score', 'Unknown') !=
                last_match_data.get('score', 'Unknown')
            )
            if existing_data_changed:
                self.match_history[match_id]['changes'].append(current_data)
                self.last_data[match_id] = state.get_last_data(current_data)
                updated_match_ids.append(match_id)
                analysis_data_changed = (
                    analysis_data_changed or existing_data_changed
                )

        if updated_match_ids:
            logger.info(
                "Matches updated: %d (%s)",
                len(updated_match_ids),
                ", ".join(updated_match_ids),
            )

        return analysis_data_changed


def parse_and_monitor_match(
    page: Page,
    match_ids: list[str] | None = None,
    saved_state: dict[str, Any] | None = None,
    *,
    live_refresh_callback: Callable[[Page], None],
    page_refresh_callback: Callable[[Page], None] | None = None,
    restart_deadline: float | None = None,
    shutdown_requested: Event | None = None,
    saved_state_path: str | Path | None = None,
) -> None:
    """Запустить мониторинг матчей с новым или восстановленным состоянием."""
    MatchMonitor(
        page,
        match_ids=match_ids,
        saved_state=saved_state,
        live_refresh_callback=live_refresh_callback,
        page_refresh_callback=page_refresh_callback,
        restart_deadline=restart_deadline,
        shutdown_requested=shutdown_requested,
        saved_state_path=saved_state_path,
    ).run()


__all__ = [
    'parse_and_monitor_match',
    'MatchMonitor',
    'PageRestartRequired',
]