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
            const match = onclick.match(/soccerInPage\.detail\([^,]+,"([^"]*)","([^"]*)","([^"]*)"\)/);
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
