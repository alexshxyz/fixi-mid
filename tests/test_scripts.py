from pathlib import Path

from app.monitoring import scripts


JS_DIR = Path(scripts.__file__).parent / "js"


def test_browser_scripts_are_loaded_from_javascript_files():
    assert {
        path.name for path in JS_DIR.glob("*.js")
    } == {
        "collect_match_status.js",
        "extract_all_match_data.js",
        "install_live_change_observer.js",
        "live_change_predicate.js",
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
    assert scripts.COLLECT_MATCH_STATUS == (
        JS_DIR / "collect_match_status.js"
    ).read_text(encoding="utf-8").removesuffix("\n")
    assert scripts.EXTRACT_ALL_MATCH_DATA == (
        JS_DIR / "extract_all_match_data.js"
    ).read_text(encoding="utf-8").removesuffix("\n")
    assert "?.textContent.trim() || '-'" in scripts.EXTRACT_ALL_MATCH_DATA
    assert "?.textContent.trim() || '?'" in scripts.EXTRACT_ALL_MATCH_DATA


def test_collect_match_status_marks_each_match_active_from_its_own_id_and_odds():
    script = scripts.COLLECT_MATCH_STATUS
    assert "row.id.match(/^tr1_(.+)$/)" in script
    assert "row.querySelectorAll('p.odds1, p.odds3')" in script
    assert "active: Boolean(matchId && hasOdds)" in script
    assert "globalReady" not in script
    assert "hasCrownOdds" not in script
    assert "requireCrown" not in script
