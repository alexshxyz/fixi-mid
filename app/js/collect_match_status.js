(requireCrown) => {
    __LIVE_ODDS_READINESS_HELPER__
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
        const homeTeam = row.querySelector('a[id^="team1_"]')?.textContent.trim() || 'Unknown';
        const awayTeam = row.querySelector('a[id^="team2_"]')?.textContent.trim() || 'Unknown';

        result.push({
            match_id: matchId,
            label: `${homeTeam} - ${awayTeam}`,
            active: Boolean(matchId && hasOdds && globalReady)
        });
    }

    return result;
}
