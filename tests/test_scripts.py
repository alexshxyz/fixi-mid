from app import scripts


def test_live_odds_scripts_use_the_shared_readiness_criteria():
    readiness_scripts = (
        scripts.LIVE_ODDS_READINESS,
        scripts.COLLECT_MATCH_STATUS,
    )

    for script in readiness_scripts:
        assert "const getLiveOddsReadiness = () =>" in script
        assert script.count("document.querySelectorAll('td.oddstd[onclick]')") == 1
        assert script.count("document.querySelectorAll('td.oddstd')") == 1
        assert "getLiveOddsReadiness()" in script

    assert "return {hasCrownOdds, hasVisibleOdds};" in scripts.LIVE_ODDS_READINESS
    assert "hasCrownOdds && hasVisibleOdds" not in scripts.LIVE_ODDS_READINESS
