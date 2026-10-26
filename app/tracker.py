import json
import os
import time
from .config import (
    DATA_DIR,
    DEBUGMODE,
    HARD_PAGE_REFRESH_MAX_RETRIES,
    HARD_PAGE_REFRESH_RETRY_DELAY_SECONDS,
    HEARTBEAT_INTERVAL_SECONDS,
    LEAGUES_LIST,
    NOTIFICATION_CHECK_DELAY_SECONDS,
    NO_MATCH_RETRY_DELAY_SECONDS,
    PAGE_LIVE_RELOAD,
    PAGE_REFRESH_FAILURE_RETRY_DELAY_SECONDS,
    RESTART_HOURS,
    STATE_SAVE_FILE,
    get_table_live_reload_interval,
    TELEGRAM_EDIT_MAX_ATTEMPTS,
    TELEGRAM_EDIT_RETRY_DELAY_SECONDS,
)
from .logger import setup_logger
from .closed_ov_ah import find_pattern_matches
from .notifier import edit_telegram_notification
from .storage import update_match_mark
from .observer import (
    PageRestartRequired,
    ScheduledRestartRequired,
    _collect_match_status,
    _extract_all_match_data,
    _install_live_change_observer,
    _reload_page_with_retries,
    _remove_live_change_observer,
    _wait_for_live_change,
    _write_match_count,
)

logger = setup_logger(__name__)


def _get_last_data(match_data):
    return {
        'ah': match_data['ah'],
        'ov': match_data['ov'],
        'match_time': match_data.get('match_time', 'Unknown'),
        'score': match_data.get('score', 'Unknown'),
    }


# Убирает из истории анализа записи без изменений отслеживаемых полей.
def _get_analysis_history(match_history):
    analysis_history = {}
    tracked_fields = ('ah', 'ov', 'match_time', 'score')

    for match_id, match_data in match_history.items():
        changes = []
        previous_entry = match_data.get('initial')
        for entry in match_data.get('changes', []):
            if previous_entry is None or any(
                entry.get(field, 'Unknown') !=
                previous_entry.get(field, 'Unknown')
                for field in tracked_fields
            ):
                changes.append(entry)
                previous_entry = entry

        analysis_history[match_id] = {
            **match_data,
            'changes': changes,
        }

    return analysis_history


# Загружает сохранённое состояние матча из JSON-файла.
def load_state_from_json(path=STATE_SAVE_FILE):
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            saved_state = json.load(f)
        os.remove(path)
        logger.info(f"Loaded and removed saved state file {path}")
        return saved_state
    except Exception as e:
        logger.error(f"Failed to load state from {path}: {e}")
        return None


# Создаёт монитор и инициализирует состояние и расписание проверок.
class MatchMonitor:
    # Создаёт монитор и инициализирует состояние и расписание проверок.
    def __init__(
        self,
        page,
        match_ids=None,
        saved_state=None,
        *,
        live_refresh_callback,
        page_refresh_callback=None,
        restart_deadline=None,
        shutdown_requested=None,
    ):
        self.page = page
        self.match_ids = match_ids
        self.saved_state = saved_state
        self.live_refresh_callback = live_refresh_callback
        self.page_refresh_callback = page_refresh_callback
        self.shutdown_requested = shutdown_requested
        self.match_history = {}
        self.last_data = {}
        self.pending_notifications = {}
        self.active_match_ids = []
        self.next_reload_at = time.monotonic() + get_table_live_reload_interval()
        self.next_page_refresh_at = time.monotonic() + PAGE_LIVE_RELOAD
        self.next_heartbeat_at = time.monotonic() + HEARTBEAT_INTERVAL_SECONDS
        self.restart_deadline = (
            restart_deadline
            if restart_deadline is not None
            else time.monotonic() + RESTART_HOURS * 3600
        )

    # Запускает цикл мониторинга для текущего списка матчей.
    def run(self):
        try:
            self._init_or_restore_state()
            logger.info("Monitoring started")
            _install_live_change_observer(self.page)
            try:
                self._monitor_loop()
            finally:
                _remove_live_change_observer(self.page)
        except PageRestartRequired:
            raise
        except Exception as e:
            if self._shutdown_is_requested():
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

    # Проверяет, запрошено ли завершение мониторинга.
    def _shutdown_is_requested(self):
        return (
            self.shutdown_requested is not None
            and self.shutdown_requested.is_set()
        )

    # Сохраняет текущее состояние матчей в JSON.
    def _save_state_to_json(self, active_match_ids, last_data, path=STATE_SAVE_FILE):
        try:
            payload = {
                "match_history": self.match_history,
                "last_data": last_data,
                "active_match_ids": active_match_ids,
                "pending_notifications": self.pending_notifications,
            }
            with open(path, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
            logger.info(f"Saved state to {path}")
            return True
        except Exception as e:
            logger.error(f"Failed to save state to {path}: {e}")
            return False

    # Восстанавливает состояние из файла или инициализирует его заново.
    def _init_or_restore_state(self):
        if self.saved_state:
            restored_history = self.saved_state.get("match_history", {})
            self.match_history.update(restored_history)
            for match_data in self.match_history.values():
                entries = [match_data.get("initial"), *match_data.get("changes", [])]
                for entry in entries:
                    if entry is not None:
                        entry.setdefault("date", None)
            self.last_data = self.saved_state.get("last_data", {})
            for match_id, match_data in self.match_history.items():
                last_data = self.last_data.get(match_id)
                if last_data is None or 'score' in last_data:
                    continue
                changes = match_data.get('changes', [])
                latest_entry = changes[-1] if changes else match_data.get('initial', {})
                if 'score' in latest_entry:
                    last_data['score'] = latest_entry['score']
            self.pending_notifications = self.saved_state.get("pending_notifications", {})
            restored_match_ids = list(dict.fromkeys(
                [
                    *self.saved_state.get("active_match_ids", []),
                    *self.match_history.keys(),
                ]
            ))
            self.active_match_ids = restored_match_ids
            if self.match_ids is not None:
                self._synchronize_matches(self.match_ids)
            return

        self.active_match_ids = list(self.match_ids or [])
        if self.active_match_ids:
            self._load_initial_data()

        self._run_analyzer()

    # Сохраняет передаваемую анализатору историю в локальный отладочный файл.
    def _run_analyzer(self):
        analysis_history = _get_analysis_history(self.match_history)
        if DEBUGMODE == 1:
            snapshot_path = DATA_DIR / "data_closed_ov_ah.json"
            try:
                snapshot_path.parent.mkdir(parents=True, exist_ok=True)
                with open(snapshot_path, "w", encoding="utf-8") as snapshot_file:
                    json.dump(
                        analysis_history,
                        snapshot_file,
                        ensure_ascii=False,
                        indent=2,
                    )
                    snapshot_file.write("\n")
            except OSError:
                logger.exception("Failed to write analyzer input snapshot to %s", snapshot_path)

        find_pattern_matches(
            analysis_history,
            self._register_pending_notification,
        )

    # Регистрирует отдельный таймер для отправленного сообщения.
    def _register_pending_notification(self, notification):
        message_id = str(notification['message_id'])
        notification['deadline'] = time.time() + NOTIFICATION_CHECK_DELAY_SECONDS
        self.pending_notifications[message_id] = notification
        logger.info(
            "Scheduled Telegram status check for match %s (%s) in %d seconds",
            notification['match_id'],
            notification['market'],
            NOTIFICATION_CHECK_DELAY_SECONDS,
        )

    # Проверяет истёкшие таймеры и обновляет сообщения в Telegram.
    def _process_due_notifications(self):
        now = time.time()
        for message_id, notification in list(self.pending_notifications.items()):
            if notification['deadline'] > now:
                continue

            if 'edited_message' not in notification:
                match_id = str(notification['match_id'])
                market = notification['market']
                field_name = 'over' if market == 'ov' else 'ah'
                last_market_data = self.last_data.get(match_id, {}).get(market, {})
                closed_remains = last_market_data.get(field_name) == 'Closed'
                league_is_listed = notification['league'] in LEAGUES_LIST

                if league_is_listed:
                    marker = '🔥' if closed_remains else '⭐️'
                else:
                    marker = '🔒' if closed_remains else '💩'

                message = notification['message']
                notification['edited_message'] = message.replace(
                    '🔎 ', f'{marker} ', 1
                )
                notification['mark'] = marker

            if edit_telegram_notification(
                notification['message_id'],
                notification['edited_message'],
                notification.get('channel_id'),
            ):
                if notification.get('link') and notification.get('prediction') and notification.get('mark'):
                    if not update_match_mark(
                        notification['link'],
                        notification['prediction'],
                        notification['mark'],
                    ):
                        logger.error(
                            "Failed to persist Telegram mark for message %s",
                            message_id,
                        )
                del self.pending_notifications[message_id]
                match_id = str(notification['match_id'])
                self._discard_match_data_if_unused(match_id)
            else:
                notification['edit_attempts'] = notification.get('edit_attempts', 0) + 1
                if notification['edit_attempts'] >= TELEGRAM_EDIT_MAX_ATTEMPTS:
                    del self.pending_notifications[message_id]
                    match_id = str(notification['match_id'])
                    self._discard_match_data_if_unused(match_id)
                    logger.error(
                        "Giving up Telegram status edit for message %s after %s attempts",
                        message_id,
                        TELEGRAM_EDIT_MAX_ATTEMPTS,
                    )
                else:
                    notification['deadline'] = now + TELEGRAM_EDIT_RETRY_DELAY_SECONDS
                    logger.warning(
                        "Retrying Telegram status edit for message %s in %s seconds",
                        message_id,
                        TELEGRAM_EDIT_RETRY_DELAY_SECONDS,
                    )

    def _discard_match_data_if_unused(self, match_id):
        if match_id in self.active_match_ids:
            return
        if any(
            str(item['match_id']) == match_id
            for item in self.pending_notifications.values()
        ):
            return
        self.last_data.pop(match_id, None)

    # Возвращает время до ближайшей отложенной проверки.
    def _next_notification_timeout(self):
        if not self.pending_notifications:
            return None
        return max(
            0,
            min(item['deadline'] for item in self.pending_notifications.values()) - time.time(),
        )

    # Загружает стартовые данные для всех активных матчей.
    def _load_initial_data(self):
        initial_all_data = _extract_all_match_data(self.page, self.active_match_ids)

        for match_id in self.active_match_ids:
            initial_data = initial_all_data.get(match_id)
            if initial_data:
                self.match_history[match_id] = {'initial': initial_data, 'changes': []}
                self.last_data[match_id] = _get_last_data(initial_data)
            else:
                logger.info(f"No initial data for match {match_id}")

    # Ждёт мутации Live-таблицы и периодически обновляет её.
    def _monitor_loop(self):
        while not self._shutdown_is_requested():
            data_changed = False
            now = time.monotonic()
            self._restart_if_due()
            if self._shutdown_is_requested():
                break
            self._process_due_notifications()
            self._restart_if_due()
            if self._shutdown_is_requested():
                break

            if now >= self.next_heartbeat_at:
                logger.info("Heartbeat: OK")
                self.next_heartbeat_at = now + HEARTBEAT_INTERVAL_SECONDS

            if self.page_refresh_callback and now >= self.next_page_refresh_at:
                try:
                    data_changed = self._do_scheduled_page_refresh()
                    self._restart_if_due()
                    self.next_reload_at = (
                        time.monotonic() + get_table_live_reload_interval()
                    )
                    self.next_page_refresh_at = (
                        time.monotonic() + PAGE_LIVE_RELOAD
                    )
                except PageRestartRequired:
                    raise
                except Exception as e:
                    logger.error("Scheduled page refresh failed: %s", e)
                    self.next_page_refresh_at = (
                        time.monotonic() + PAGE_REFRESH_FAILURE_RETRY_DELAY_SECONDS
                    )

                if data_changed:
                    self._run_analyzer()
                if self._shutdown_is_requested():
                    break
                self._process_due_notifications()
                continue

            if now >= self.next_reload_at:
                data_changed = self._do_periodic_reload()
                self._restart_if_due()
                if self._shutdown_is_requested():
                    break
                if self.active_match_ids:
                    data_changed = (
                        self._poll_and_update(log_data_loaded=True)
                        or data_changed
                    )
                    self._restart_if_due()
                    if self._shutdown_is_requested():
                        break
                if data_changed:
                    self._run_analyzer()
                if self._shutdown_is_requested():
                    break
                self._process_due_notifications()
                self._restart_if_due()
                continue

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
            if self.shutdown_requested is None:
                changed = _wait_for_live_change(
                    self.page,
                    timeout_ms=max(1, int(wait_seconds * 1000)),
                )
            else:
                changed = _wait_for_live_change(
                    self.page,
                    timeout_ms=max(1, int(wait_seconds * 1000)),
                    shutdown_requested=self.shutdown_requested,
                )
            if self._shutdown_is_requested():
                break
            self._restart_if_due()
            if changed and self.active_match_ids:
                data_changed = self._poll_and_update()
                self._restart_if_due()

            if data_changed:
                self._run_analyzer()
            self._process_due_notifications()
            self._restart_if_due()

    # Проверяет, наступил ли срок планового перезапуска.
    def _restart_if_due(self):
        if self._shutdown_is_requested():
            return
        if time.monotonic() >= self.restart_deadline:
            self._trigger_scheduled_restart()

    # Сохраняет состояние и запускает перезапуск по расписанию.
    def _trigger_scheduled_restart(self):
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

    # Выполняет регулярное обновление Live-таблицы и синхронизацию матчей.
    def _do_periodic_reload(self):
        live_refresh_succeeded = _reload_page_with_retries(
            self.page,
            self.live_refresh_callback,
        )
        if live_refresh_succeeded is False:
            match_status = _collect_match_status(self.page, require_crown=True)
        elif live_refresh_succeeded is None:
            logger.warning("Live refresh failed. Escalating to a Page refresh.")
            match_status = self._do_hard_page_refresh_with_retries()
        else:
            match_status = _collect_match_status(self.page, require_crown=True)

        if not any(item['active'] for item in match_status):
            match_status = self._wait_for_matches_with_hard_refresh(match_status)

        current_match_ids = [
            item['match_id'] for item in match_status if item['active']
        ]
        synchronized = self._synchronize_matches(current_match_ids)
        _write_match_count(match_status, self.active_match_ids)
        self.next_reload_at = time.monotonic() + get_table_live_reload_interval()
        return synchronized

    # Ждёт появления валидных матчей, повторяя жёсткую перезагрузку раз в минуту.
    def _wait_for_matches_with_hard_refresh(self, match_status=None):
        if match_status is None:
            match_status = _collect_match_status(self.page, require_crown=True)

        while not any(item['active'] for item in match_status):
            self._restart_if_due()
            logger.info(
                "No matches found. Retrying in %s seconds...",
                NO_MATCH_RETRY_DELAY_SECONDS,
            )
            time.sleep(
                max(
                    0,
                    min(
                        NO_MATCH_RETRY_DELAY_SECONDS,
                        self.restart_deadline - time.monotonic(),
                    ),
                )
            )
            self._restart_if_due()
            match_status = self._do_hard_page_refresh_with_retries()
            self._restart_if_due()
        return match_status

    # Перезагружает страницу, не пересоздавая состояние монитора.
    def _do_scheduled_page_refresh(self):
        match_status = self._do_hard_page_refresh_with_retries()
        match_status = self._wait_for_matches_with_hard_refresh(match_status)
        current_match_ids = [
            item['match_id'] for item in match_status if item['active']
        ]
        data_changed = self._synchronize_matches(current_match_ids)
        _write_match_count(match_status, self.active_match_ids)
        if self.active_match_ids:
            data_changed = (
                self._poll_and_update(log_data_loaded=True)
                or data_changed
            )
        return data_changed

    # Повторяет перезагрузку страницы и сохраняет состояние при отказе.
    def _do_hard_page_refresh_with_retries(
        self,
        max_retries=HARD_PAGE_REFRESH_MAX_RETRIES,
        retry_delay=HARD_PAGE_REFRESH_RETRY_DELAY_SECONDS,
    ):
        for attempt in range(1, max_retries + 1):
            self._restart_if_due()
            try:
                if self.page_refresh_callback is None:
                    raise RuntimeError("Page refresh callback is not configured")
                self.page_refresh_callback(self.page, wait_for_data=False)
                self._restart_if_due()
                _install_live_change_observer(self.page)
                match_status = _collect_match_status(self.page, require_crown=True)
                self._restart_if_due()
                return match_status
            except PageRestartRequired:
                raise
            except Exception as e:
                if attempt < max_retries:
                    logger.warning(
                        "Page refresh attempt %s/%s failed: %s. "
                        "Retrying in %s seconds...",
                        attempt,
                        max_retries,
                        e,
                        retry_delay,
                    )
                    time.sleep(
                        max(
                            0,
                            min(
                                retry_delay,
                                self.restart_deadline - time.monotonic(),
                            ),
                        )
                    )
                    self._restart_if_due()
                    continue

                if self._save_state_to_json(self.active_match_ids, self.last_data):
                    logger.error(
                        "Page refresh failed after %s attempts. "
                        "Saved state to %s and requesting restart.",
                        max_retries,
                        STATE_SAVE_FILE,
                    )
                else:
                    logger.error(
                        "Page refresh failed after %s attempts and state save "
                        "failed. Requesting restart anyway.",
                        max_retries,
                    )
                raise PageRestartRequired(
                    f"Page refresh failed after {max_retries} attempts: {e}"
                ) from e

    # Синхронизирует набор активных матчей после обновления таблицы.
    def _synchronize_matches(self, current_match_ids):
        current_match_ids = list(dict.fromkeys(current_match_ids))
        previous_match_ids = set(self.active_match_ids)
        current_match_id_set = set(current_match_ids)
        new_match_ids = [match_id for match_id in current_match_ids if match_id not in previous_match_ids]
        removed_match_ids = [match_id for match_id in self.active_match_ids if match_id not in current_match_id_set]

        for removed_id in removed_match_ids:
            self.match_history.pop(removed_id, None)
            if not any(
                str(item['match_id']) == str(removed_id)
                for item in self.pending_notifications.values()
            ):
                self.last_data.pop(removed_id, None)

        initialized_match_ids = [match_id for match_id in current_match_ids if match_id in previous_match_ids]
        if new_match_ids:
            new_data = _extract_all_match_data(self.page, new_match_ids)

            for new_id in new_match_ids:
                initial_data = new_data.get(new_id)
                if initial_data:
                    self.match_history[new_id] = {'initial': initial_data, 'changes': []}
                    self.last_data[new_id] = _get_last_data(initial_data)
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

    # Проверяет изменение данных по текущим матчам и обновляет историю.
    def _poll_and_update(self, log_data_loaded=False):
        all_match_data = _extract_all_match_data(self.page, self.active_match_ids)

        if log_data_loaded:
            logger.info("Matches data reloaded")

        updated_match_ids = []
        analysis_data_changed = False
        for match_id in self.active_match_ids:
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
                self.last_data[match_id] = _get_last_data(current_data)
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


# Запускает мониторинг матчей с новым или восстановленным состоянием.
def parse_and_monitor_match(
    page,
    match_ids=None,
    saved_state=None,
    *,
    live_refresh_callback,
    page_refresh_callback=None,
    restart_deadline=None,
    shutdown_requested=None,
):
    MatchMonitor(
        page,
        match_ids=match_ids,
        saved_state=saved_state,
        live_refresh_callback=live_refresh_callback,
        page_refresh_callback=page_refresh_callback,
        restart_deadline=restart_deadline,
        shutdown_requested=shutdown_requested,
    ).run()


# Экспорт функций
__all__ = [
    'parse_and_monitor_match',
    'MatchMonitor',
    'PageRestartRequired',
]