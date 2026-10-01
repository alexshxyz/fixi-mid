import time

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from config import STATE_SAVE_FILE
from logger import setup_logger

logger = setup_logger('tracker')


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
            const observationRoot = document.documentElement;

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


# Обновляет Live-таблицу с повторными попытками до успешного результата.
def _reload_page_with_retries(
    page,
    active_match_ids,
    last_data,
    save_state,
    live_refresh_callback,
    max_crash_retries=3,
    max_timeout_retries=4,
):
    crash_retries = 0
    timeout_retries = 0
    while True:
        try:
            live_refresh_callback(page)
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