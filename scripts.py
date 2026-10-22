# Общий критерий готовности Live-коэффициентов для всех JS-проверок.
_LIVE_ODDS_READINESS_HELPER = """
        const getLiveOddsReadiness = () => {
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
        };
    """

# Проверяет наличие Crown и видимых коэффициентов.
HAS_VALID_MATCH_DATA = """
        () => {
""" + _LIVE_ODDS_READINESS_HELPER + """
            const {hasCrownOdds, hasVisibleOdds} = getLiveOddsReadiness();
            return hasCrownOdds && hasVisibleOdds;
        }
    """

# Устанавливает наблюдатель за изменениями Live-таблицы.
INSTALL_LIVE_CHANGE_OBSERVER = """
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
    """

# Отключает наблюдатель и очищает его флаги.
REMOVE_LIVE_CHANGE_OBSERVER = """
            () => {
                const observerKey = "__fixi_live_table_observer";
                const changeFlag = "__fixi_live_table_changed";
                window[observerKey]?.disconnect();
                delete window[observerKey];
                delete window[changeFlag];
            }
        """

# Проверяет, изменился ли DOM Live-таблицы.
LIVE_CHANGE_PREDICATE = "() => window.__fixi_live_table_changed === true"

# Сбрасывает флаг изменения Live-таблицы.
RESET_LIVE_CHANGE_FLAG = "window.__fixi_live_table_changed = false"

# Проверяет наличие Crown и видимых коэффициентов после обновления.
LIVE_ODDS_READINESS = """
                () => {
""" + _LIVE_ODDS_READINESS_HELPER + """
                    return getLiveOddsReadiness();
                }
                """

# Собирает видимые матчи и отмечает их готовность к наблюдению.
COLLECT_MATCH_STATUS = """
        (requireCrown) => {
""" + _LIVE_ODDS_READINESS_HELPER + """
            const oddsReadiness = getLiveOddsReadiness();
            const globalReady = !requireCrown || (
                oddsReadiness.hasCrownOdds && oddsReadiness.hasVisibleOdds
            );
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
    """

# Извлекает коэффициенты и сведения по переданным ID матчей.
EXTRACT_ALL_MATCH_DATA = """
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
                    league: league
                };
            }
            
            return result;
        }
    """
