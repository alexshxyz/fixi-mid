const getLiveOddsReadiness = () => {
    const hasCrownOdds = Array.from(
        document.querySelectorAll('td.oddstd[onclick]')
    ).some(cell => /,\s*["']3["']\s*,/.test(
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
