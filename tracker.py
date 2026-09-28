"""
Основная логика парсинга одного матча.
Координация: загрузка страницы, инициализация браузера, вызов парсеров и отправка результатов.
"""
import json
import os
import time
import random
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from config import (
    LEAGUES_LIST,
    NOTIFICATION_CHECK_DELAY_SECONDS,
    PAGE_RELOAD_MAX_SECONDS,
    PAGE_RELOAD_MIN_SECONDS,
    RESTART_HOURS,
    STATE_SAVE_FILE,
)
from logger import setup_logger
from analyzer import find_pattern_matches
from notifier import edit_telegram_notification

logger = setup_logger(__name__)

# Исключение для перезапуска браузера после повторных падений страницы.
class PageRestartRequired(Exception):
    pass


# Устанавливает наблюдатель за изменениями Live-таблицы в DOM.
def _install_live_change_observer(page):
    page.evaluate("""
        () => {
            const observerKey = "__fixi_live_table_observer";
            const changeFlag = "__fixi_live_table_changed";
            const table = document.querySelector('table#table_live');
            if (!table) {
                throw new Error('Live table not found.');
            }
            const observationRoot = table.parentElement || table;

            const belongsToLiveTable = (node) => {
                const element = node.nodeType === Node.ELEMENT_NODE
                    ? node
                    : node.parentElement;
                return Boolean(element && element.closest('table#table_live'));
            };
            const containsLiveTable = (node) => {
                const element = node.nodeType === Node.ELEMENT_NODE
                    ? node
                    : node.parentElement;
                return Boolean(
                    element && (
                        element.matches('table#table_live') ||
                        element.querySelector('table#table_live')
                    )
                );
            };

            window[changeFlag] = false;
            const observer = new MutationObserver((records) => {
                const liveTableChanged = records.some((record) => {
                    if (belongsToLiveTable(record.target)) return true;
                    return [...record.addedNodes, ...record.removedNodes].some(
                        (node) => belongsToLiveTable(node) || containsLiveTable(node)
                    );
                });
                if (liveTableChanged) window[changeFlag] = true;
            });
            observer.observe(observationRoot, {
                childList: true,
                subtree: true,
                characterData: true,
                attributes: true,
                attributeFilter: ['class', 'style'],
            });
            window[observerKey] = observer;
        }
    """)


# Отключает наблюдатель за Live-таблицей и удаляет его флаги из страницы.
def _remove_live_change_observer(page):
    try:
        page.evaluate("""
            () => {
                const observerKey = "__fixi_live_table_observer";
                const changeFlag = "__fixi_live_table_changed";
                window[observerKey]?.disconnect();
                delete window[observerKey];
                delete window[changeFlag];
            }
        """)
    except Exception:
        logger.warning("Could not stop live table observer.", exc_info=True)


# Ждёт изменения Live-таблицы и возвращает, произошло ли оно до таймаута.
def _wait_for_live_change(page, timeout_ms):
    try:
        page.wait_for_function(
            "() => window.__fixi_live_table_changed === true",
            timeout=timeout_ms,
            polling=100,
        )
    except PlaywrightTimeoutError:
        return False

    page.evaluate("window.__fixi_live_table_changed = false")
    return True


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


# Обновляет Live-таблицу кликом по фильтру без полной перезагрузки страницы.
def _refresh_live_table(page):
    live_filter = page.locator("li#li_FilterLive")
    live_filter.wait_for(timeout=10000)
    live_filter.click()
    page.locator("table#table_live").wait_for(timeout=10000)
    page.wait_for_timeout(1000)


# Обновляет Live-таблицу с повторными попытками до успешного результата.
def _reload_page_with_retries(page, active_match_ids, last_data, save_state, max_crash_retries=3, max_timeout_retries=4):
    crash_retries = 0
    timeout_retries = 0
    while True:
        try:
            _refresh_live_table(page)
            data_ready = page.evaluate(
                """
                () => {
                    const hasCrownOdds = Array.from(
                        document.querySelectorAll('td.oddstd[onclick]')
                    ).some(cell => /,\\s*["']3["']\\s*,/.test(
                        cell.getAttribute('onclick') || ''
                    ));

                    const hasVisibleOddsPair = Array.from(
                        document.querySelectorAll('td.oddstd')
                    ).some(cell => {
                        if (cell.offsetParent === null) return false;
                        const odds1 = cell.querySelector('p.odds1');
                        const odds3 = cell.querySelector('p.odds3');
                        return odds1 && odds3 &&
                            odds1.offsetParent !== null &&
                            odds3.offsetParent !== null;
                    });

                    return {hasCrownOdds, hasVisibleOddsPair};
                }
                """
            )

            if not data_ready["hasCrownOdds"] or not data_ready["hasVisibleOddsPair"]:
                logger.warning(
                    "Matches not found after reload. "
                    "Waiting 60 seconds before reloading again..."
                )
                time.sleep(60)
                continue

            logger.info("Live table refreshed")
            return
        except Exception as e:
            error_text = str(e)
            if "Page.reload: Page crashed" in error_text or "Page crashed" in error_text:
                crash_retries += 1
                timeout_retries = 0  # Сброс timeout retries при crash
            else:
                timeout_retries += 1
                crash_retries = 0  # Сброс crash retries при timeout

            if crash_retries >= max_crash_retries:
                if save_state(active_match_ids, last_data):
                    logger.error(f"Page crashed {crash_retries} times. Saved state to {STATE_SAVE_FILE} and requesting restart.")
                else:
                    logger.error(f"Page crashed {crash_retries} times and state save failed. Requesting restart anyway.")
                raise PageRestartRequired(f"Page crashed {crash_retries} times during reload")

            if timeout_retries >= max_timeout_retries:
                if save_state(active_match_ids, last_data):
                    logger.error(f"Reload timed out {timeout_retries} times in a row. Saved state to {STATE_SAVE_FILE} and requesting restart.")
                else:
                    logger.error(f"Reload timed out {timeout_retries} times and state save failed. Requesting restart anyway.")
                raise PageRestartRequired(f"Reload timed out {timeout_retries} times in a row during reload")

            logger.error(f"Reload failed: {e}. Retrying in 3 seconds...")
            time.sleep(3)


# Извлекает данные ВСЕ матчей за один evaluate() вызов.
def _extract_all_match_data(page, match_ids):
    js = """
        (matchIds) => {
            const result = {};
            
            for (const match_id of matchIds) {
                const timeElem = document.querySelector('td#time_' + match_id);
                if (!timeElem) {
                    result[match_id] = null;
                    continue;
                }
                
                const row = timeElem.closest('tr');
                if (!row) {
                    result[match_id] = null;
                    continue;
                }
                
                const tds = Array.from(row.querySelectorAll('td.oddstd'));
                if (tds.length < 3) {
                    result[match_id] = null;
                    continue;
                }
                
                const normalize = (el) => {
                    if (!el) return '-';
                    const value = el.textContent.trim();
                    return value === '' ? '-' : value;
                };
                
                const odds1 = [];
                const odds3 = [];
                
                tds.slice(0, 3).forEach(td => {
                    odds1.push(normalize(td.querySelector('p.odds1')));
                    odds3.push(normalize(td.querySelector('p.odds3')));
                });
                
                const timeTd = document.querySelector('td#time_' + match_id);
                const onclick = timeTd.getAttribute('onclick');
                let league = 'Unknown';
                let team1 = row.querySelector('a[id="team1_' + match_id + '"]')?.textContent.trim() || 'Unknown';
                let team2 = row.querySelector('a[id="team2_' + match_id + '"]')?.textContent.trim() || 'Unknown';
                const gotSpan = row.querySelector('span[id="got_' + match_id + '"]');
                let match_time = gotSpan?.textContent.trim() || 'Unknown';

                if (!gotSpan || !gotSpan.textContent.trim()) {
                    const statusCell = row.querySelector('td#time_' + match_id);
                    const statusText = statusCell?.textContent.trim();
                    if (statusText && statusText !== 'Unknown') {
                        match_time = statusText;
                    }
                }
                
                if (onclick) {
                    const match = onclick.match(/soccerInPage\\.detail\\([^,]+,"([^"]*)","([^"]*)","([^"]*)"\\)/);
                    if (match) {
                        team1 = match[1] || team1;
                        team2 = match[2] || team2;
                        league = match[3] || league;
                    }
                }
                
                result[match_id] = {
                    time: timeElem.textContent.trim() || 'Unknown',
                    match_time: match_time,
                    ah: {
                        home_ah_odds: odds1[0],
                        ah: odds1[1],
                        away_ah_odds: odds1[2]
                    },
                    ov: {
                        over_odds: odds3[0],
                        over: odds3[1],
                        under_odds: odds3[2]
                    },
                    team1: team1,
                    team2: team2,
                    score: row.querySelector('td.blue.handpoint[onclick*="soccerInPage.detail"]')?.textContent.trim() || 'Unknown',
                    league: league
                };
            }
            
            return result;
        }
    """
    
    try:
        handle = page.wait_for_function(js, arg=match_ids, timeout=5000)
        return handle.json_value()
    except Exception as e:
        logger.error(f"Error in _extract_all_match_data: {e}")
        raise


# Возвращает список ID матчей, которые сейчас видны в Live-таблице.
def _collect_match_ids(page):
    return page.evaluate("""
        () => {
            const matches = new Set();
            const rows = Array.from(document.querySelectorAll('table#table_live tbody tr.tds'));

            for (const row of rows) {
                if (row.offsetParent === null) continue;
                const timeElem = row.querySelector('[id^="time_"]');
                if (!timeElem || timeElem.offsetParent === null) continue;
                const matchId = timeElem.id.replace(/^time_/, '');
                if (!matchId) continue;

                const hasOdds = Array.from(row.querySelectorAll('p.odds1, p.odds3'))
                    .some(odds => odds.offsetParent !== null);
                if (hasOdds) {
                    matches.add(matchId);
                }
            }

            return Array.from(matches);
        }
    """)


# Мониторит активные матчи и отслеживает изменения в их данных.
class MatchMonitor:
    # Создаёт монитор и инициализирует состояние и расписание проверок.
    def __init__(self, page, match_ids=None, saved_state=None):
        self.page = page
        self.match_ids = match_ids
        self.saved_state = saved_state
        self.match_history = {}
        self.last_data = {}
        self.pending_notifications = {}
        self.active_match_ids = []
        self.consecutive_table_errors = 0
        self.reload_threshold = random.randint(PAGE_RELOAD_MIN_SECONDS, PAGE_RELOAD_MAX_SECONDS)
        self.next_reload_at = time.monotonic() + self.reload_threshold
        self.next_heartbeat_at = time.monotonic() + 100
        self.restart_deadline = time.time() + RESTART_HOURS * 3600

    # Запускает цикл мониторинга для текущего списка матчей.
    def run(self):
        logger.info("Monitoring started")
        try:
            self._init_or_restore_state()
            _install_live_change_observer(self.page)
            try:
                self._monitor_loop()
            finally:
                _remove_live_change_observer(self.page)
        except PageRestartRequired:
            raise
        except Exception as e:
            logger.error(f"Error in parse_and_monitor_match: {e}")

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
            self.active_match_ids = self.saved_state.get("active_match_ids", [])
            restored_history = self.saved_state.get("match_history", {})
            self.match_history.update(restored_history)
            self.last_data = self.saved_state.get("last_data", {})
            self.pending_notifications = self.saved_state.get("pending_notifications", {})
            logger.info(f"Restored state for {len(self.active_match_ids)} matches from {STATE_SAVE_FILE}")
            return

        self.active_match_ids = list(self.match_ids or [])
        if self.active_match_ids:
            self._load_initial_data()

        find_pattern_matches(self.match_history, self._register_pending_notification)

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
                    marker = '🔥' if closed_remains else '🔓'
                else:
                    marker = '⭐' if closed_remains else '💩'

                notification['edited_message'] = notification['message'].replace(
                    '<b>', f'<b>{marker} ', 1
                )

            if edit_telegram_notification(
                notification['message_id'], notification['edited_message']
            ):
                del self.pending_notifications[message_id]
                match_id = str(notification['match_id'])
                if (match_id not in self.active_match_ids and not any(
                    str(item['match_id']) == match_id
                    for item in self.pending_notifications.values()
                )):
                    self.last_data.pop(match_id, None)
            else:
                notification['edit_attempts'] = notification.get('edit_attempts', 0) + 1
                if notification['edit_attempts'] >= 3:
                    del self.pending_notifications[message_id]
                    match_id = str(notification['match_id'])
                    if (match_id not in self.active_match_ids and not any(
                        str(item['match_id']) == match_id
                        for item in self.pending_notifications.values()
                    )):
                        self.last_data.pop(match_id, None)
                    logger.error(
                        "Giving up Telegram status edit for message %s after 3 attempts",
                        message_id,
                    )
                else:
                    notification['deadline'] = now + 30
                    logger.warning(
                        "Retrying Telegram status edit for message %s in 30 seconds",
                        message_id,
                    )

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
        self.consecutive_table_errors = 0

        for match_id in self.active_match_ids:
            initial_data = initial_all_data.get(match_id)
            if initial_data:
                self.match_history[match_id] = {'initial': initial_data, 'changes': []}
                self.last_data[match_id] = {
                    'ah': initial_data['ah'],
                    'ov': initial_data['ov'],
                    'match_time': initial_data.get('match_time', 'Unknown'),
                }
            else:
                logger.info(f"No initial data for match {match_id}")

    # Ждёт мутации Live-таблицы и периодически обновляет её.
    def _monitor_loop(self):
        while True:
            data_changed = False
            now = time.monotonic()
            self._process_due_notifications()

            if now >= self.next_heartbeat_at:
                logger.info("Heartbeat: OK")
                self.next_heartbeat_at = now + 100

            if time.time() >= self.restart_deadline:
                self._trigger_scheduled_restart()

            if now >= self.next_reload_at:
                data_changed = self._do_periodic_reload()
                if self.active_match_ids:
                    changed, _ = self._poll_and_update()
                    data_changed = data_changed or changed
                if data_changed:
                    find_pattern_matches(
                        self.match_history, self._register_pending_notification
                    )
                self._process_due_notifications()
                continue

            wait_seconds = min(
                self.next_reload_at - now,
                self.restart_deadline - time.time(),
                self.next_heartbeat_at - now,
            )
            notification_timeout = self._next_notification_timeout()
            if notification_timeout is not None:
                wait_seconds = min(wait_seconds, notification_timeout)
            changed = _wait_for_live_change(
                self.page,
                timeout_ms=max(1, int(wait_seconds * 1000)),
            )
            if changed and self.active_match_ids:
                data_changed, _ = self._poll_and_update()

            if data_changed:
                find_pattern_matches(
                    self.match_history, self._register_pending_notification
                )
            self._process_due_notifications()

    # Сохраняет состояние и запускает перезапуск по расписанию.
    def _trigger_scheduled_restart(self):
        logger.info(f"Restart interval reached ({RESTART_HOURS} hours). Saving state and restarting browser...")
        if self._save_state_to_json(self.active_match_ids, self.last_data):
            logger.info("State saved successfully. Raising PageRestartRequired to restart browser.")
        else:
            logger.error("Failed to save state, but proceeding with restart.")
        raise PageRestartRequired(f"Scheduled restart after {RESTART_HOURS} hours")

    # Выполняет регулярное обновление Live-таблицы и синхронизацию матчей.
    def _do_periodic_reload(self):
        _reload_page_with_retries(
            self.page,
            self.active_match_ids,
            self.last_data,
            self._save_state_to_json,
        )

        current_match_ids = _collect_match_ids(self.page)
        synchronized = self._synchronize_matches(current_match_ids)
        self.reload_threshold = random.randint(PAGE_RELOAD_MIN_SECONDS, PAGE_RELOAD_MAX_SECONDS)
        self.next_reload_at = time.monotonic() + self.reload_threshold
        return synchronized

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
            self.consecutive_table_errors = 0

            for new_id in new_match_ids:
                initial_data = new_data.get(new_id)
                if initial_data:
                    self.match_history[new_id] = {'initial': initial_data, 'changes': []}
                    self.last_data[new_id] = {
                        'ah': initial_data['ah'],
                        'ov': initial_data['ov'],
                        'match_time': initial_data.get('match_time', 'Unknown'),
                    }
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
    def _poll_and_update(self):
        all_match_data = _extract_all_match_data(self.page, self.active_match_ids)
        self.consecutive_table_errors = 0

        updated_match_ids = []
        for match_id in self.active_match_ids:
            if match_id not in self.last_data:
                continue
            current_data = all_match_data.get(match_id)
            if not current_data:
                continue
            old_match_time = self.last_data[match_id].get('match_time', 'Unknown')
            if (current_data['ah'] != self.last_data[match_id]['ah'] or
                    current_data['ov'] != self.last_data[match_id]['ov'] or
                    current_data.get('match_time', 'Unknown') != old_match_time):
                self.match_history[match_id]['changes'].append(current_data)
                self.last_data[match_id] = {
                    'ah': current_data['ah'],
                    'ov': current_data['ov'],
                    'match_time': current_data.get('match_time', 'Unknown'),
                }
                updated_match_ids.append(match_id)

        if updated_match_ids:
            logger.info(
                "Matches updated: %d (%s)",
                len(updated_match_ids),
                ", ".join(updated_match_ids),
            )

        return bool(updated_match_ids), False


# Запускает мониторинг матчей с новым или восстановленным состоянием.
def parse_and_monitor_match(page, match_ids=None, saved_state=None):
    MatchMonitor(page, match_ids=match_ids, saved_state=saved_state).run()


# Экспорт функций
__all__ = ['parse_and_monitor_match', 'MatchMonitor', 'PageRestartRequired']