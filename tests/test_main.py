import pytest

import main
from tracker import PageRestartRequired


class FakeLocator:
    def __init__(self, *, wait_error=None, click_error=None):
        self.wait_error = wait_error
        self.click_error = click_error
        self.wait_calls = []
        self.click_calls = 0

    def wait_for(self, **kwargs):
        self.wait_calls.append(kwargs)
        if self.wait_error:
            raise self.wait_error

    def click(self):
        self.click_calls += 1
        if self.click_error:
            raise self.click_error


class FakePage:
    def __init__(self):
        self.locators = {}
        self.goto_calls = []
        self.reload_calls = []
        self.wait_timeout_calls = []
        self.evaluate_result = False
        self.evaluate_calls = []
        self.viewport_sizes = []

    def locator(self, selector):
        return self.locators.setdefault(selector, FakeLocator())

    def goto(self, *args, **kwargs):
        self.goto_calls.append((args, kwargs))

    def reload(self, **kwargs):
        self.reload_calls.append(kwargs)

    def wait_for_timeout(self, timeout):
        self.wait_timeout_calls.append(timeout)

    def evaluate(self, script):
        self.evaluate_calls.append(script)
        return self.evaluate_result

    def set_viewport_size(self, size):
        self.viewport_sizes.append(size)


class FakeBrowser:
    def __init__(self, *, context=None):
        self.context = context or FakeContext()
        self.close_calls = 0

    def new_context(self):
        return self.context

    def close(self):
        self.close_calls += 1


class FakeContext:
    def __init__(self, *, page=None, cookies_error=None):
        self.page = page or FakePage()
        self.cookies_error = cookies_error
        self.cookie_calls = []

    def new_page(self):
        return self.page

    def add_cookies(self, cookies):
        self.cookie_calls.append(cookies)
        if self.cookies_error:
            raise self.cookies_error


class FakePlaywright:
    def __init__(self, browser):
        self.chromium = self
        self.browser = browser
        self.launch_calls = []

    def launch(self, **kwargs):
        self.launch_calls.append(kwargs)
        return self.browser


@pytest.mark.parametrize(
    ("failures", "max_retries", "expected_calls"),
    [(0, 3, 1), (2, 3, 3)],
)
def test_retry_page_action_returns_result_after_retries(
    monkeypatch, failures, max_retries, expected_calls
):
    attempts = []
    refreshes = []

    def action():
        attempts.append(True)
        if len(attempts) <= failures:
            raise RuntimeError("temporary")
        return "done"

    monkeypatch.setattr(
        main, "refresh_live_table", lambda page: refreshes.append(page)
    )

    assert main._retry_page_action(
        "page",
        action,
        "action",
        max_retries=max_retries,
        reload_before_retry=True,
    ) == "done"
    assert len(attempts) == expected_calls
    assert refreshes == ["page"] * failures


def test_retry_page_action_raises_after_final_failure(monkeypatch, caplog):
    attempts = []
    monkeypatch.setattr(main, "refresh_live_table", lambda page: None)

    def action():
        attempts.append(True)
        raise ValueError("permanent")

    with pytest.raises(ValueError, match="permanent"):
        main._retry_page_action(
            None, action, "load page", max_retries=2, reload_before_retry=True
        )

    assert len(attempts) == 2
    assert "Failed to load page after 2 attempts" in caplog.text


def test_retry_page_action_logs_refresh_failure_and_retries(
    monkeypatch, caplog
):
    attempts = []

    def refresh(page):
        raise RuntimeError("refresh failed")

    def action():
        attempts.append(True)
        if len(attempts) == 1:
            raise ValueError("action failed")
        return "ok"

    monkeypatch.setattr(main, "refresh_live_table", refresh)

    assert main._retry_page_action(
        "page", action, "action", max_retries=2, reload_before_retry=True
    ) == "ok"
    assert len(attempts) == 2
    assert "Failed to refresh Live table before retry" in caplog.text


def test_init_browser_sets_up_page_and_opens_site(monkeypatch):
    page = FakePage()
    context = FakeContext(page=page)
    browser = FakeBrowser(context=context)
    playwright = FakePlaywright(browser)
    monkeypatch.setattr(main, "BROWSER_HEADLESS", True)
    monkeypatch.setattr(main, "SITE_COOKIES", [{"name": "cookie"}])
    monkeypatch.setattr(main, "SITE_URL", "https://example.test")

    result_browser, result_page = main.init_browser(playwright)

    assert result_browser is browser
    assert result_page is page
    assert playwright.launch_calls[0]["headless"] is True
    assert playwright.launch_calls[0]["proxy"] == {
        "server": "socks5://127.0.0.1:10808"
    }
    assert context.cookie_calls == [[{"name": "cookie"}]]
    assert page.viewport_sizes == [{"width": 1280, "height": 720}]
    assert page.goto_calls == [
        (
            ("https://example.test",),
            {"wait_until": "domcontentloaded", "timeout": 30000},
        )
    ]


def test_init_browser_continues_if_cookie_setup_fails(monkeypatch, caplog):
    page = FakePage()
    context = FakeContext(page=page, cookies_error=RuntimeError("cookie error"))
    browser = FakeBrowser(context=context)
    playwright = FakePlaywright(browser)
    monkeypatch.setattr(main, "_retry_page_action", lambda *args, **kwargs: None)

    assert main.init_browser(playwright) == (browser, page)
    assert "Failed to add site cookies: cookie error" in caplog.text


def test_init_browser_closes_browser_when_navigation_fails(monkeypatch):
    browser = FakeBrowser()
    playwright = FakePlaywright(browser)

    def fail_navigation(*args, **kwargs):
        raise RuntimeError("navigation failed")

    monkeypatch.setattr(main, "_retry_page_action", fail_navigation)

    with pytest.raises(RuntimeError, match="navigation failed"):
        main.init_browser(playwright)

    assert browser.close_calls == 1


def test_close_popup_clicks_button_and_swallows_missing_popup(caplog):
    page = FakePage()
    main.close_popup(page)
    assert page.locators["i.closebtn"].wait_calls == [{"timeout": 10000}]
    assert page.locators["i.closebtn"].click_calls == 1

    missing_page = FakePage()
    missing_page.locators["i.closebtn"] = FakeLocator(
        wait_error=TimeoutError("not found")
    )
    main.close_popup(missing_page)
    assert "Popup did not appear" in caplog.text


def test_switch_to_live_waits_clicks_and_propagates_failure():
    page = FakePage()

    main.switch_to_live(page)

    assert page.locators["li#li_FilterLive"].click_calls == 1
    assert page.locators["table#table_live"].wait_calls == [{"timeout": 10000}]

    failing_page = FakePage()
    failing_page.locators["table#table_live"] = FakeLocator(
        wait_error=RuntimeError("table missing")
    )
    with pytest.raises(RuntimeError, match="table missing"):
        main.switch_to_live(failing_page)


def test_refresh_live_table_clicks_waits_and_delays():
    page = FakePage()

    main.refresh_live_table(page)

    assert page.locators["li#li_FilterLive"].click_calls == 1
    assert page.locators["table#table_live"].wait_calls == [{"timeout": 10000}]
    assert page.wait_timeout_calls == [1000]


def test_refresh_page_without_data_wait_reloads_and_returns():
    page = FakePage()

    assert main.refresh_page(page, wait_for_data=False) is None
    assert page.reload_calls == [
        {"wait_until": "domcontentloaded", "timeout": 30000}
    ]
    assert page.locators["table#table_live"].wait_calls == [{"timeout": 20000}]
    assert page.wait_timeout_calls == []


def test_refresh_page_waits_until_data_is_valid(monkeypatch):
    page = FakePage()
    outcomes = iter([False, True])
    monkeypatch.setattr(main, "has_valid_match_data", lambda current: next(outcomes))

    assert main.refresh_page(page) is None
    assert page.wait_timeout_calls == [1000]


def test_refresh_page_raises_timeout_when_data_never_becomes_ready(monkeypatch):
    page = FakePage()
    ticks = iter([0, 61])
    monkeypatch.setattr(main.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(main, "has_valid_match_data", lambda current: False)

    with pytest.raises(TimeoutError, match="Live table data did not become ready"):
        main.refresh_page(page)

    assert page.wait_timeout_calls == []


def test_refresh_page_propagates_reload_failure():
    class BrokenPage(FakePage):
        def reload(self, **kwargs):
            raise RuntimeError("reload failed")

    with pytest.raises(RuntimeError, match="reload failed"):
        main.refresh_page(BrokenPage())


def test_collect_matches_deduplicates_active_ids_and_writes_debug_snapshot(
    monkeypatch,
):
    page = FakePage()
    status = [
        {"match_id": "1", "active": True, "label": "A - B"},
        {"match_id": "1", "active": True, "label": "A - B"},
        {"match_id": "2", "active": False, "label": "C - D"},
    ]
    writes = []
    monkeypatch.setattr(main, "_collect_match_status", lambda *args, **kwargs: status)
    monkeypatch.setattr(main, "DEBUGMODE", 1)
    monkeypatch.setattr(
        main, "_write_match_count", lambda *args: writes.append(args)
    )

    assert main.collect_matches(page) == ["1"]
    assert page.wait_timeout_calls == [1000]
    assert writes == [(status, ["1"])]


def test_collect_matches_swallows_collection_error_and_skips_debug_write(
    monkeypatch,
):
    page = FakePage()
    writes = []
    monkeypatch.setattr(
        main,
        "_collect_match_status",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("collection failed")),
    )
    monkeypatch.setattr(main, "DEBUGMODE", 1)
    monkeypatch.setattr(main, "_write_match_count", lambda *args: writes.append(args))

    assert main.collect_matches(page) == []
    assert writes == []


def test_has_valid_match_data_waits_and_returns_page_evaluation():
    page = FakePage()
    page.evaluate_result = True

    assert main.has_valid_match_data(page) is True
    assert page.wait_timeout_calls == [1000]
    assert "hasCrownOdds" in page.evaluate_calls[0]
    assert "hasVisibleOdds" in page.evaluate_calls[0]


class FakeSyncPlaywright:
    def __init__(self, playwright):
        self.playwright = playwright

    def __enter__(self):
        return self.playwright

    def __exit__(self, exc_type, exc, traceback):
        return False


class StopMain(Exception):
    pass


def configure_main(monkeypatch, *, saved_state=None, page=None, browser=None):
    page = page or FakePage()
    browser = browser or FakeBrowser(context=FakeContext(page=page))
    playwright = FakePlaywright(browser)
    monkeypatch.setattr(main, "init_storage", lambda: True)
    monkeypatch.setattr(main, "sync_playwright", lambda: FakeSyncPlaywright(playwright))
    monkeypatch.setattr(main, "init_browser", lambda p: (browser, page))
    monkeypatch.setattr(main, "load_state_from_json", lambda: saved_state)
    monkeypatch.setattr(main, "switch_to_live", lambda page: None)
    return page, browser


def test_main_starts_monitor_with_saved_state(monkeypatch):
    saved_state = {"active_match_ids": ["1"]}
    page, browser = configure_main(monkeypatch, saved_state=saved_state)
    calls = []
    monkeypatch.setattr(
        main,
        "_collect_match_status",
        lambda *args, **kwargs: [{"active": True}],
    )

    def stop_after_monitor(page, **kwargs):
        calls.append((page, kwargs))
        raise StopMain

    monkeypatch.setattr(main, "parse_and_monitor_match", stop_after_monitor)

    with pytest.raises(StopMain):
        main.main()

    assert calls == [
        (
            page,
            {
                "saved_state": saved_state,
                "live_refresh_callback": main.refresh_live_table,
                "page_refresh_callback": main.refresh_page,
            },
        )
    ]
    assert browser.close_calls == 1


def test_main_waits_for_saved_match_and_refreshes_page(monkeypatch):
    saved_state = {"active_match_ids": ["1"]}
    page, _ = configure_main(monkeypatch, saved_state=saved_state)
    statuses = iter([[{"active": False}], [{"active": True}]])
    sleeps = []
    refreshes = []
    calls = []
    monkeypatch.setattr(
        main, "_collect_match_status", lambda *args, **kwargs: next(statuses)
    )
    monkeypatch.setattr(main.time, "sleep", sleeps.append)
    monkeypatch.setattr(
        main, "refresh_page", lambda *args, **kwargs: refreshes.append((args, kwargs))
    )
    monkeypatch.setattr(
        main,
        "parse_and_monitor_match",
        lambda *args, **kwargs: (calls.append((args, kwargs)), (_ for _ in ()).throw(StopMain))[1],
    )

    with pytest.raises(StopMain):
        main.main()

    assert sleeps == [60]
    assert refreshes == [((page,), {"wait_for_data": False})]
    assert len(calls) == 1


def test_main_starts_monitor_with_collected_matches(monkeypatch):
    page, _ = configure_main(monkeypatch)
    calls = []
    monkeypatch.setattr(main, "collect_matches", lambda current: ["1", "2"])
    monkeypatch.setattr(
        main,
        "parse_and_monitor_match",
        lambda *args, **kwargs: (calls.append((args, kwargs)), (_ for _ in ()).throw(StopMain))[1],
    )

    with pytest.raises(StopMain):
        main.main()

    assert calls == [
        (
            (page, ["1", "2"]),
            {
                "live_refresh_callback": main.refresh_live_table,
                "page_refresh_callback": main.refresh_page,
            },
        )
    ]


def test_main_waits_and_refreshes_until_matches_appear(monkeypatch):
    page, _ = configure_main(monkeypatch)
    observed_matches = iter([[], ["match-1"]])
    sleeps = []
    refreshes = []
    calls = []
    monkeypatch.setattr(main, "collect_matches", lambda current: next(observed_matches))
    monkeypatch.setattr(main.time, "sleep", sleeps.append)
    monkeypatch.setattr(
        main, "refresh_page", lambda *args, **kwargs: refreshes.append((args, kwargs))
    )
    monkeypatch.setattr(
        main,
        "parse_and_monitor_match",
        lambda *args, **kwargs: (calls.append(args), (_ for _ in ()).throw(StopMain))[1],
    )

    with pytest.raises(StopMain):
        main.main()

    assert sleeps == [60]
    assert refreshes == [((page,), {"wait_for_data": False})]
    assert calls == [(page, ["match-1"])]


def test_main_restarts_process_on_page_restart_required(monkeypatch):
    page, browser = configure_main(
        monkeypatch, saved_state={"active_match_ids": ["1"]}
    )
    exec_calls = []

    def restart(executable, args):
        exec_calls.append((executable, args))
        raise RuntimeError("exec called")

    monkeypatch.setattr(
        main,
        "parse_and_monitor_match",
        lambda *args, **kwargs: (_ for _ in ()).throw(PageRestartRequired("restart")),
    )
    monkeypatch.setattr(
        main, "_collect_match_status", lambda *args, **kwargs: [{"active": True}]
    )
    monkeypatch.setattr(main.os, "execv", restart)
    monkeypatch.setattr(main.sys, "executable", "python.exe")
    monkeypatch.setattr(main.sys, "argv", ["main.py", "--flag"])

    with pytest.raises(RuntimeError, match="exec called"):
        main.main()

    assert browser.close_calls == 1
    assert exec_calls == [("python.exe", ["python.exe", "main.py", "--flag"])]


def test_main_closes_browser_and_reraises_unexpected_failure(monkeypatch):
    _, browser = configure_main(
        monkeypatch, saved_state={"active_match_ids": ["1"]}
    )
    monkeypatch.setattr(
        main, "_collect_match_status", lambda *args, **kwargs: [{"active": True}]
    )
    monkeypatch.setattr(
        main,
        "parse_and_monitor_match",
        lambda *args, **kwargs: (_ for _ in ()).throw(ValueError("monitor failed")),
    )

    with pytest.raises(ValueError, match="monitor failed"):
        main.main()

    assert browser.close_calls == 1


def test_main_ignores_browser_close_error_during_restart(monkeypatch):
    class BrokenBrowser(FakeBrowser):
        def close(self):
            self.close_calls += 1
            raise RuntimeError("close failed")

    browser = BrokenBrowser()
    configure_main(
        monkeypatch,
        saved_state={"active_match_ids": ["1"]},
        browser=browser,
    )
    monkeypatch.setattr(
        main,
        "parse_and_monitor_match",
        lambda *args, **kwargs: (_ for _ in ()).throw(PageRestartRequired("restart")),
    )
    monkeypatch.setattr(
        main, "_collect_match_status", lambda *args, **kwargs: [{"active": True}]
    )
    monkeypatch.setattr(
        main.os, "execv", lambda *args: (_ for _ in ()).throw(RuntimeError("exec"))
    )

    with pytest.raises(RuntimeError, match="exec"):
        main.main()

    assert browser.close_calls == 1
