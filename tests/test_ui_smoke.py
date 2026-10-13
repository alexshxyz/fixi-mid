import pytest
from playwright.sync_api import sync_playwright

import main


@pytest.mark.smoke
def test_real_site_loads_and_live_table_is_active():
    with sync_playwright() as playwright:
        browser, page = main.init_browser(playwright)
        try:
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
        finally:
            browser.close()
