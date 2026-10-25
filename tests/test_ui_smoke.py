import json

import pytest
from playwright.sync_api import sync_playwright

from app import main
from app.observer import _extract_all_match_data


@pytest.mark.smoke
def test_real_site_loads_and_live_table_is_active():
    with sync_playwright() as playwright:
        browser, page = main.init_browser(playwright)
        try:
            loaded_cookies = {
                cookie["name"]: cookie["value"]
                for cookie in page.context.cookies(main.SITE_URL)
            }
            missing_cookies = [
                cookie["name"]
                for cookie in main.SITE_COOKIES
                if loaded_cookies.get(cookie["name"]) != cookie["value"]
            ]
            assert not missing_cookies, (
                "Configured site cookies were not loaded: "
                f"{', '.join(missing_cookies)}"
            )
            print(f"Site cookies loaded: {len(main.SITE_COOKIES)}")

            main.switch_to_live(page)

            page.locator("li#li_FilterLive.on").wait_for(
                state="visible",
                timeout=10_000,
            )
            live_table = page.locator("table#table_live")
            live_table.wait_for(state="visible", timeout=10_000)
            assert live_table.is_visible()

            assert main.has_valid_match_data(page), (
                "Live table did not expose Crown odds and visible odds"
            )
            match_status = main._collect_match_status(page, require_crown=True)
            matches = list(dict.fromkeys(
                item["match_id"]
                for item in match_status
                if item["active"]
            ))
            print(f"Matches: {len(matches)}")
            assert matches, "No active matches found on the Live page"

            first_match_id = matches[0]
            parsed_matches = _extract_all_match_data(page, [first_match_id])
            first_match = parsed_matches.get(first_match_id)
            assert first_match is not None, (
                f"Could not parse data for first active match {first_match_id}"
            )

            required_fields = {
                "time",
                "match_time",
                "ah",
                "ov",
                "team1",
                "team2",
                "score",
                "league",
                "date",
            }
            assert required_fields <= first_match.keys()
            assert first_match["ah"].keys() >= {
                "home_ah_odds",
                "ah",
                "away_ah_odds",
            }
            assert first_match["ov"].keys() >= {
                "over_odds",
                "over",
                "under_odds",
            }

            parsed_values = [
                first_match[field]
                for field in ("time", "match_time", "team1", "team2", "score", "league")
            ]
            parsed_values.extend(first_match["ah"].values())
            parsed_values.extend(first_match["ov"].values())
            assert all(
                isinstance(value, str) and value.strip() not in ("", "Unknown", "-")
                for value in parsed_values
            ), f"First active match contains missing parsed data: {first_match}"
            print(
                f"First match {first_match_id} parsed: "
                f"{json.dumps(first_match, ensure_ascii=False, indent=2)}"
            )
        finally:
            browser.close()
