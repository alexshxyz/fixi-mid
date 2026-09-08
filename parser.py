"""
Основная логика парсинга одного матча.
Координация: загрузка страницы, инициализация браузера, вызов парсеров и отправка результатов.
"""
import json
import os
import time
import random
from logging_config import setup_logger
from logics import find_pattern_matches

logger = setup_logger(__name__)

STATE_SAVE_FILE = "match_state.json"
RESTART_HOURS = 8  # Число часов до сохранения состояния и «рестарта"


class PageRestartRequired(Exception):
    """Raised when the page crashes repeatedly and the script needs to restart."""
    pass


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



def _reload_page_with_retries(page, active_match_ids, last_data, save_state, max_crash_retries=3, max_timeout_retries=4):
    crash_retries = 0
    timeout_retries = 0
    while True:
        try:
            page.reload(wait_until='domcontentloaded')
            page.wait_for_timeout(1000)
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
                    "Crown odds or visible odds pair did not appear after reload. "
                    "Reloading again..."
                )
                continue

            logger.info("Page reloaded and Crown odds are ready")
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


def _extract_all_match_data(page, match_ids):
    """
    Извлекает данные ВСЕ матчей за один evaluate() вызов.
    Вместо 70 evaluate, делаем 1 — это главная оптимизация.
    """
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
                    score: row.querySelector('td.f-b.blue.handpoint')?.textContent.trim() || 'Unknown',
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


class MatchMonitor:
    def __init__(self, page, match_ids=None, saved_state=None):
        self.page = page
        self.match_ids = match_ids
        self.saved_state = saved_state
        self.match_history = {}
        self.last_data = {}
        self.active_match_ids = []
        self.consecutive_table_errors = 0
        self.reload_counter = 0
        self.reload_threshold = random.randint(420, 480)
        self.restart_deadline = time.time() + RESTART_HOURS * 3600

    def run(self):
        logger.info("parse_and_monitor_match started")
        try:
            self._init_or_restore_state()
            self._monitor_loop()
        except PageRestartRequired:
            raise
        except Exception as e:
            logger.error(f"Error in parse_and_monitor_match: {e}")

    def _save_state_to_json(self, active_match_ids, last_data, path=STATE_SAVE_FILE):
        try:
            payload = {
                "match_history": self.match_history,
                "last_data": last_data,
                "active_match_ids": active_match_ids,
            }
            with open(path, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
            logger.info(f"Saved state to {path}")
            return True
        except Exception as e:
            logger.error(f"Failed to save state to {path}: {e}")
            return False

    def _init_or_restore_state(self):
        if self.saved_state:
            self.active_match_ids = self.saved_state.get("active_match_ids", [])
            restored_history = self.saved_state.get("match_history", {})
            self.match_history.update(restored_history)
            self.last_data = self.saved_state.get("last_data", {})
            logger.info(f"Restored state for {len(self.active_match_ids)} matches from {STATE_SAVE_FILE}")
            return

        self.active_match_ids = list(self.match_ids or [])
        logger.info(f"Initializing monitoring for {len(self.active_match_ids)} matches")
        if self.active_match_ids:
            self._load_initial_data()

        logger.info("Initial call to find_pattern_matches")
        find_pattern_matches(self.match_history)

    def _load_initial_data(self):
        initial_all_data = _extract_all_match_data(self.page, self.active_match_ids)
        self.consecutive_table_errors = 0

        for match_id in self.active_match_ids:
            initial_data = initial_all_data.get(match_id)
            if initial_data:
                self.match_history[match_id] = {'initial': initial_data, 'changes': []}
                self.last_data[match_id] = {'ah': initial_data['ah'], 'ov': initial_data['ov']}
                logger.info(f"Initial data loaded for match {match_id}")
            else:
                logger.info(f"No initial data for match {match_id}")

    def _monitor_loop(self):
        loop_counter = 0
        while True:
            time.sleep(1)
            self.reload_counter += 1
            loop_counter += 1
            data_changed = False

            if loop_counter % 100 == 0:
                logger.info(f"Heartbeat: {loop_counter} loops completed")

            if time.time() >= self.restart_deadline:
                self._trigger_scheduled_restart()

            if self.reload_counter >= self.reload_threshold:
                if self._do_periodic_reload():
                    data_changed = True

            if self.active_match_ids:
                changed, should_continue = self._poll_and_update()
                if should_continue:
                    continue
                data_changed = data_changed or changed

            if data_changed:
                find_pattern_matches(self.match_history)

    def _trigger_scheduled_restart(self):
        logger.info(f"Restart interval reached ({RESTART_HOURS} hours). Saving state and restarting browser...")
        if self._save_state_to_json(self.active_match_ids, self.last_data):
            logger.info("State saved successfully. Raising PageRestartRequired to restart browser.")
        else:
            logger.error("Failed to save state, but proceeding with restart.")
        raise PageRestartRequired(f"Scheduled restart after {RESTART_HOURS} hours")

    def _do_periodic_reload(self):
        self.reload_counter = 0
        self.reload_threshold = random.randint(420, 480)
        _reload_page_with_retries(
            self.page,
            self.active_match_ids,
            self.last_data,
            self._save_state_to_json,
        )

        current_match_ids = _collect_match_ids(self.page)
        return self._synchronize_matches(current_match_ids)

    def _synchronize_matches(self, current_match_ids):
        current_match_ids = list(dict.fromkeys(current_match_ids))
        previous_match_ids = set(self.active_match_ids)
        current_match_id_set = set(current_match_ids)
        new_match_ids = [match_id for match_id in current_match_ids if match_id not in previous_match_ids]
        removed_match_ids = [match_id for match_id in self.active_match_ids if match_id not in current_match_id_set]

        for removed_id in removed_match_ids:
            self.match_history.pop(removed_id, None)
            self.last_data.pop(removed_id, None)
            logger.info(f"Match {removed_id} removed")

        initialized_match_ids = [match_id for match_id in current_match_ids if match_id in previous_match_ids]
        if new_match_ids:
            new_data = _extract_all_match_data(self.page, new_match_ids)
            self.consecutive_table_errors = 0

            for new_id in new_match_ids:
                initial_data = new_data.get(new_id)
                if initial_data:
                    self.match_history[new_id] = {'initial': initial_data, 'changes': []}
                    self.last_data[new_id] = {'ah': initial_data['ah'], 'ov': initial_data['ov']}
                    initialized_match_ids.append(new_id)
                    logger.info(f"New match {new_id} added")

        self.active_match_ids = initialized_match_ids
        logger.info(
            f"Matches synchronized: active={len(self.active_match_ids)}, "
            f"new={len(new_match_ids)}, removed={len(removed_match_ids)}"
        )
        return bool(new_match_ids or removed_match_ids)

    def _poll_and_update(self):
        all_match_data = _extract_all_match_data(self.page, self.active_match_ids)
        self.consecutive_table_errors = 0

        data_changed = False
        for match_id in self.active_match_ids:
            if match_id not in self.last_data:
                continue
            current_data = all_match_data.get(match_id)
            if not current_data:
                continue
            if (current_data['ah'] != self.last_data[match_id]['ah'] or
                    current_data['ov'] != self.last_data[match_id]['ov']):
                self.match_history[match_id]['changes'].append(current_data)
                self.last_data[match_id] = {'ah': current_data['ah'], 'ov': current_data['ov']}
                data_changed = True
                logger.info(f"Match {match_id} updated")

        return data_changed, False


def parse_and_monitor_match(page, match_ids=None, saved_state=None):
    """
    Парсит и мониторит все матчи по списку ID.
    Сохраняет начальные и измененные данные в памяти.
    """
    MatchMonitor(page, match_ids=match_ids, saved_state=saved_state).run()


# Экспорт функций
__all__ = ['parse_and_monitor_match', 'MatchMonitor', 'PageRestartRequired']