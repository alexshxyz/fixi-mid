from pathlib import Path

from app import scripts


JS_DIR = Path(scripts.__file__).parent / "js"


def test_browser_scripts_are_loaded_from_javascript_files():
    assert {
        path.name for path in JS_DIR.glob("*.js")
    } == {
        "collect_match_status.js",
        "extract_all_match_data.js",
        "install_live_change_observer.js",
        "live_change_predicate.js",
        "live_odds_readiness_helper.js",
        "remove_live_change_observer.js",
        "reset_live_change_flag.js",
    }

    assert scripts.INSTALL_LIVE_CHANGE_OBSERVER == (
        JS_DIR / "install_live_change_observer.js"
    ).read_text(encoding="utf-8").removesuffix("\n")
    assert scripts.REMOVE_LIVE_CHANGE_OBSERVER == (
        JS_DIR / "remove_live_change_observer.js"
    ).read_text(encoding="utf-8").removesuffix("\n")
    assert scripts.LIVE_CHANGE_PREDICATE == (
        JS_DIR / "live_change_predicate.js"
    ).read_text(encoding="utf-8").removesuffix("\n")
    assert scripts.RESET_LIVE_CHANGE_FLAG == (
        JS_DIR / "reset_live_change_flag.js"
    ).read_text(encoding="utf-8").removesuffix("\n")
    assert "__LIVE_ODDS_READINESS_HELPER__" not in scripts.COLLECT_MATCH_STATUS
    assert "const getLiveOddsReadiness = () =>" in scripts.COLLECT_MATCH_STATUS
    assert scripts.EXTRACT_ALL_MATCH_DATA == (
        JS_DIR / "extract_all_match_data.js"
    ).read_text(encoding="utf-8").removesuffix("\n")


def test_live_odds_scripts_use_the_shared_readiness_criteria():
    script = scripts.COLLECT_MATCH_STATUS
    assert "const getLiveOddsReadiness = () =>" in script
    assert script.count("document.querySelectorAll('td.oddstd[onclick]')") == 1
    assert script.count("document.querySelectorAll('td.oddstd')") == 1
    assert "getLiveOddsReadiness()" in script
    assert "hasCrownOdds && hasVisibleOdds" in script
