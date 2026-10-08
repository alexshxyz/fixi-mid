import json
import os
import time
from datetime import datetime

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from config import DEBUGMODE
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


# Обновляет Live-таблицу с ограниченным числом повторных попыток.
def _reload_page_with_retries(
    page,
    live_refresh_callback,
    max_retries=3,
    retry_delay=5,
):
    for attempt in range(1, max_retries + 1):
        try:
            live_refresh_callback(page)
            match_status = _collect_match_status(page, require_crown=True)
            if not any(item['active'] for item in match_status):
                return False

            data_ready = page.evaluate(
                """
                () => {
                    const hasCrownOdds = Array.from(
                        document.querySelectorAll('td.oddstd[onclick]')
                    ).some(cell => /,\\s*["']3["']\\s*,/.test(
                        cell.getAttribute('onclick') || ''
                    ));

                    const hasVisibleOdds = Array.from(
                        document.querySelectorAll('td.oddstd')
                    ).some(cell => {
                        if (cell.offsetParent === null) return false;
                        const odds1 = cell.querySelector('p.odds1');
                        const odds3 = cell.querySelector('p.odds3');
                        return (odds1 && odds1.offsetParent !== null) ||
                            (odds3 && odds3.offsetParent !== null);
                    });

                    return {hasCrownOdds, hasVisibleOdds};
                }
                """
            )

            if not data_ready["hasCrownOdds"] or not data_ready["hasVisibleOdds"]:
                raise RuntimeError("Live table data is not ready after live refresh")

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
    return page.evaluate("""
        (requireCrown) => {
            const hasCrownOdds = Array.from(
                document.querySelectorAll('td.oddstd[onclick]')
            ).some(cell => /,\\s*["']3["']\\s*,/.test(
                cell.getAttribute('onclick') || ''
            ));
            const hasVisibleOdds = Array.from(
                document.querySelectorAll('td.oddstd')
            ).some(cell => {
                if (cell.offsetParent === null) return false;
                const odds1 = cell.querySelector('p.odds1');
                const odds3 = cell.querySelector('p.odds3');
                return (odds1 && odds1.offsetParent !== null) ||
                    (odds3 && odds3.offsetParent !== null);
            });
            const globalReady = !requireCrown ||
                (hasCrownOdds && hasVisibleOdds);
            const rows = Array.from(document.querySelectorAll('table#table_live tbody tr.tds'));
            const result = [];

            for (const row of rows) {
                if (row.offsetParent === null) continue;
                const match = row.id.match(/^tr1_(.+)$/);
                const matchId = match ? match[1] : null;

                const hasOdds = Array.from(row.querySelectorAll('p.odds1, p.odds3'))
                    .some(odds => odds.offsetParent !== null);
                const team1 = row.querySelector('a[id^="team1_"]')?.textContent.trim() || 'Unknown';
                const team2 = row.querySelector('a[id^="team2_"]')?.textContent.trim() || 'Unknown';

                result.push({
                    match_id: matchId,
                    label: `${team1} - ${team2}`,
                    active: Boolean(matchId && hasOdds && globalReady)
                });
            }

            return result;
        }
    """, require_crown)


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
    js = """
        (matchIds) => {
            const result = {};
            
            for (const match_id of matchIds) {
                const row = document.getElementById('tr1_' + match_id);
                if (!row) {
                    result[match_id] = null;
                    continue;
                }

                const timeElem = document.querySelector('td#time_' + match_id);
                
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
                
                const timeTd = row.querySelector('td#time_' + match_id);
                const onclick = timeTd?.getAttribute('onclick') ||
                    row.querySelector('[onclick*="soccerInPage.detail"]')?.getAttribute('onclick');
                let league = 'Unknown';
                let team1 = row.querySelector('a[id="team1_' + match_id + '"]')?.textContent.trim() || 'Unknown';
                let team2 = row.querySelector('a[id="team2_' + match_id + '"]')?.textContent.trim() || 'Unknown';
                const gotSpan = row.querySelector('span[id="got_' + match_id + '"]');
                let match_time = gotSpan?.textContent.trim() || 'Unknown';

                if (!gotSpan || !gotSpan.textContent.trim()) {
                    const statusCell = timeTd;
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

                const countRedCards = (side) => {
                    const cell = row.querySelector('td[id="' + side + '_' + match_id + '"]');
                    if (!cell) return 0;

                    return Array.from(cell.querySelectorAll('.redcard')).reduce(
                        (total, card) => {
                            const count = Number.parseInt(card.textContent.trim(), 10);
                            return Number.isFinite(count) ? total + count : total;
                        },
                        0
                    );
                };
                
                result[match_id] = {
                    time: timeElem?.textContent.trim() || 'Unknown',
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
                    league: league,
                    redcard: countRedCards('ht') + countRedCards('gt')
                };
            }
            
            return result;
        }
    """
    
    try:
        match_data = page.evaluate(js, match_ids)
    except Exception as e:
        logger.error(f"Error in _extract_all_match_data: {e}")
        raise

    observed_at = datetime.now().astimezone().isoformat(timespec="seconds")
    for entry in match_data.values():
        if entry is not None:
            entry["date"] = observed_at

    return match_data