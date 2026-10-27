import pytest

from app import main
from app.tracker import PageRestartRequired


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

    def screenshot(self, *, path):
        with open(path, "wb") as screenshot_file:
            screenshot_file.write(b"fake screenshot")


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
    sleeps = []
    monkeypatch.setattr(main.time, "sleep", sleeps.append)

    assert main._retry_page_action(
        "page",
        action,
        "action",
        max_retries=max_retries,
        reload_before_retry=True,
    ) == "done"
    assert len(attempts) == expected_calls
    assert refreshes == ["page"] * failures
    assert sleeps == [10] * failures


def test_retry_page_action_raises_after_final_failure(monkeypatch, caplog):
    attempts = []
    monkeypatch.setattr(main, "refresh_live_table", lambda page: None)
    sleeps = []
    monkeypatch.setattr(main.time, "sleep", sleeps.append)

    def action():
        attempts.append(True)
        raise ValueError("permanent")

    with pytest.raises(ValueError, match="permanent"):
        main._retry_page_action(
            None, action, "load page", max_retries=2, reload_before_retry=True
        )

    assert len(attempts) == 2
    assert sleeps == [10]
    assert "Failed to load page after 2 attempts" in caplog.text


def test_retry_page_action_stops_when_shutdown_interrupts_retry_wait(monkeypatch):
    shutdown_requested = main.threading.Event()
    attempts = []

    def interrupt_wait(seconds):
        shutdown_requested.set()
        return True

    monkeypatch.setattr(shutdown_requested, "wait", interrupt_wait)

    def action():
        attempts.append(True)
        raise RuntimeError("temporary")

    with pytest.raises(main.PageRestartRequired, match="Shutdown requested"):
        main._retry_page_action(
            "page",
            action,
            "load page",
            max_retries=2,
            shutdown_requested=shutdown_requested,
        )

    assert len(attempts) == 1


def test_retry_page_action_stops_when_action_finishes_after_shutdown(monkeypatch):
    shutdown_requested = main.threading.Event()
    attempts = []

    def action():
        attempts.append(True)
        shutdown_requested.set()
        return "done"

    with pytest.raises(main.PageRestartRequired, match="Shutdown requested"):
        main._retry_page_action(
            "page",
            action,
            "prepare page",
            shutdown_requested=shutdown_requested,
        )

    assert len(attempts) == 1


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
    sleeps = []
    monkeypatch.setattr(main.time, "sleep", sleeps.append)

    assert main._retry_page_action(
        "page", action, "action", max_retries=2, reload_before_retry=True
    ) == "ok"
    assert len(attempts) == 2
    assert sleeps == [10]
    assert "Failed to refresh Live table before retry" in caplog.text


def test_init_browser_sets_up_page_and_opens_site(monkeypatch):
    page = FakePage()
    context = FakeContext(page=page)
    browser = FakeBrowser(context=context)
    playwright = FakePlaywright(browser)
    monkeypatch.setattr(main, "BROWSER_HEADLESS", True)
    monkeypatch.setattr(main, "BROWSER_PROXY_SERVER", "socks5://proxy.test:1080")
    monkeypatch.setattr(main, "SITE_COOKIES", [{"name": "cookie"}])
    monkeypatch.setattr(main, "SITE_URL", "https://example.test")

    result_browser, result_page = main.init_browser(playwright)

    assert result_browser is browser
    assert result_page is page
    assert playwright.launch_calls[0]["headless"] is True
    assert playwright.launch_calls[0]["proxy"] == {
        "server": "socks5://proxy.test:1080"
    }
    assert context.cookie_calls == [[{"name": "cookie"}]]
    assert page.viewport_sizes == [{"width": 1280, "height": 720}]
    assert page.goto_calls == [
        (
            ("https://example.test",),
            {"wait_until": "domcontentloaded", "timeout": 30000},
        )
    ]


def test_validate_configuration_rejects_missing_browser_proxy(monkeypatch):
    monkeypatch.setattr(main, "BOT_TOKEN", "test-token")
    monkeypatch.setattr(main, "CHANNEL_ID", "test-channel")
    monkeypatch.setattr(main, "BROWSER_PROXY_SERVER", None)

    with pytest.raises(RuntimeError, match="BROWSER_PROXY_SERVER"):
        main._validate_configuration()


def test_init_browser_continues_if_cookie_setup_fails(monkeypatch, caplog):
    page = FakePage()
    context = FakeContext(page=page, cookies_error=RuntimeError("cookie error"))
    browser = FakeBrowser(context=context)
    playwright = FakePlaywright(browser)
    monkeypatch.setattr(main, "BROWSER_PROXY_SERVER", "socks5://proxy.test:1080")
    monkeypatch.setattr(main, "_retry_page_action", lambda *args, **kwargs: None)

    assert main.init_browser(playwright) == (browser, page)
    assert "Failed to add site cookies: cookie error" in caplog.text


def test_init_browser_closes_after_shutdown_during_browser_launch():
    shutdown_requested = main.threading.Event()
    browser = FakeBrowser()

    class InterruptedPlaywright(FakePlaywright):
        def launch(self, **kwargs):
            shutdown_requested.set()
            return browser

    with pytest.raises(main.PageRestartRequired, match="Shutdown requested"):
        main.init_browser(
            InterruptedPlaywright(browser),
            shutdown_requested=shutdown_requested,
        )

    assert browser.close_calls == 1
    assert browser.context.page.goto_calls == []


def test_init_browser_converts_launch_failure_after_shutdown(monkeypatch):
    shutdown_requested = main.threading.Event()

    class InterruptedPlaywright:
        chromium = None

        def __init__(self):
            self.chromium = self

        def launch(self, **kwargs):
            shutdown_requested.set()
            raise RuntimeError("launch interrupted")

    with pytest.raises(main.PageRestartRequired, match="browser launch"):
        main.init_browser(
            InterruptedPlaywright(),
            shutdown_requested=shutdown_requested,
        )


def test_init_browser_restarts_after_navigation_retries_fail(monkeypatch):
    attempts = []
    sleeps = []
    restart_calls = []
    browser = FakeBrowser()
    playwright = FakePlaywright(browser)
    monkeypatch.setattr(main, "BROWSER_PROXY_SERVER", "socks5://proxy.test:1080")
    monkeypatch.setattr(main.time, "sleep", sleeps.append)
    def fail_goto(*args, **kwargs):
        attempts.append((args, kwargs))
        raise RuntimeError("navigation failed")

    monkeypatch.setattr(FakePage, "goto", fail_goto)

    class RestartRequested(Exception):
        pass

    def restart(restarting_browser):
        restart_calls.append(restarting_browser)
        restarting_browser.close()
        raise RestartRequested

    monkeypatch.setattr(main, "_restart_script", restart)

    with pytest.raises(RestartRequested):
        main.init_browser(playwright)

    assert len(attempts) == 2
    assert sleeps == [10]
    assert restart_calls == [browser]
    assert browser.close_calls == 1


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


def test_refresh_live_table_logs_and_reraises_failure(
    monkeypatch, tmp_path, caplog
):
    page = FakePage()
    page.locators["table#table_live"] = FakeLocator(
        wait_error=RuntimeError("table unavailable")
    )
    monkeypatch.setattr(
        main,
        "ERROR_SCREENSHOTS_DIR",
        str(tmp_path / "logs" / "screenshots"),
    )

    with pytest.raises(RuntimeError, match="table unavailable"):
        main.refresh_live_table(page)

    assert "Failed to refresh Live table" in caplog.text


def test_refresh_live_table_saves_screenshot_after_failure(
    monkeypatch, tmp_path, caplog
):
    page = FakePage()
    page.locators["li#li_FilterLive"] = FakeLocator(
        wait_error=TimeoutError("filter not visible")
    )
    screenshots_dir = tmp_path / "logs" / "screenshots"
    monkeypatch.setattr(main, "ERROR_SCREENSHOTS_DIR", str(screenshots_dir))

    with pytest.raises(TimeoutError, match="filter not visible"):
        main.refresh_live_table(page)

    screenshots = list(screenshots_dir.glob("*.png"))
    assert len(screenshots) == 1
    assert "ERROR - Failed to refresh Live table" in screenshots[0].name
    assert screenshots[0].read_bytes() == b"fake screenshot"
    assert "Saved error screenshot" in caplog.text


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


def test_refresh_page_raises_timeout_when_data_never_becomes_ready(
    monkeypatch, tmp_path
):
    page = FakePage()
    ticks = iter([0, 61])
    monkeypatch.setattr(main.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(main, "has_valid_match_data", lambda current: False)
    monkeypatch.setattr(
        main,
        "ERROR_SCREENSHOTS_DIR",
        str(tmp_path / "logs" / "screenshots"),
    )

    with pytest.raises(TimeoutError, match="Live table data did not become ready"):
        main.refresh_page(page)

    assert page.wait_timeout_calls == []


def test_refresh_page_propagates_reload_failure(monkeypatch, tmp_path):
    class BrokenPage(FakePage):
        def reload(self, **kwargs):
            raise RuntimeError("reload failed")

    monkeypatch.setattr(
        main,
        "ERROR_SCREENSHOTS_DIR",
        str(tmp_path / "logs" / "screenshots"),
    )

    with pytest.raises(RuntimeError, match="reload failed"):
        main.refresh_page(BrokenPage())


def test_refresh_page_saves_screenshot_after_failure(monkeypatch, tmp_path):
    class BrokenPage(FakePage):
        def reload(self, **kwargs):
            raise RuntimeError("navigation timeout")

    screenshots_dir = tmp_path / "logs" / "screenshots"
    monkeypatch.setattr(main, "ERROR_SCREENSHOTS_DIR", str(screenshots_dir))

    with pytest.raises(RuntimeError, match="navigation timeout"):
        main.refresh_page(BrokenPage())

    screenshots = list(screenshots_dir.glob("*.png"))
    assert len(screenshots) == 1
    assert "ERROR - Failed to refresh page" in screenshots[0].name
    assert screenshots[0].read_bytes() == b"fake screenshot"


def test_refresh_page_skips_screenshot_when_page_is_closed(
    monkeypatch, tmp_path
):
    class ClosedPage(FakePage):
        def reload(self, **kwargs):
            raise RuntimeError(
                "Page.reload: Target page, context or browser has been closed"
            )

    screenshots_dir = tmp_path / "logs" / "screenshots"
    monkeypatch.setattr(main, "ERROR_SCREENSHOTS_DIR", str(screenshots_dir))

    with pytest.raises(RuntimeError, match="has been closed"):
        main.refresh_page(ClosedPage())

    assert not screenshots_dir.exists()


def test_screenshot_failure_does_not_hide_refresh_error(
    monkeypatch, tmp_path, caplog
):
    class BrokenScreenshotPage(FakePage):
        def reload(self, **kwargs):
            raise RuntimeError("navigation timeout")

        def screenshot(self, *, path):
            raise RuntimeError("screenshot unavailable")

    monkeypatch.setattr(
        main, "ERROR_SCREENSHOTS_DIR", str(tmp_path / "screenshots")
    )

    with pytest.raises(RuntimeError, match="navigation timeout"):
        main.refresh_page(BrokenScreenshotPage())

    assert "Failed to capture screenshot" in caplog.text


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
    monkeypatch.setattr(main, "collect_match_status", lambda *args, **kwargs: status)
    monkeypatch.setattr(main, "DEBUGMODE", 1)
    monkeypatch.setattr(
        main, "write_match_count", lambda *args: writes.append(args)
    )

    assert main.collect_matches(page) == ["1"]
    assert page.wait_timeout_calls == [1000]
    assert writes == [(status, ["1"])]


def test_collect_matches_logs_collection_error_and_skips_debug_write(
    monkeypatch, caplog
):
    page = FakePage()
    writes = []
    monkeypatch.setattr(
        main,
        "collect_match_status",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("collection failed")),
    )
    monkeypatch.setattr(main, "DEBUGMODE", 1)
    monkeypatch.setattr(main, "write_match_count", lambda *args: writes.append(args))

    assert main.collect_matches(page) == []
    assert writes == []
    assert "Failed to collect matches" in caplog.text
    assert "collection failed" in caplog.text


def test_has_valid_match_data_waits_and_returns_page_evaluation():
    page = FakePage()
    page.evaluate_result = {
        "hasCrownOdds": True,
        "hasVisibleOdds": True,
    }

    assert main.has_valid_match_data(page) is True
    assert page.wait_timeout_calls == [1000]
    assert "hasCrownOdds" in page.evaluate_calls[0]
    assert "hasVisibleOdds" in page.evaluate_calls[0]


@pytest.mark.parametrize(
    ("has_crown_odds", "has_visible_odds"),
    [(False, True), (True, False), (False, False)],
)
def test_has_valid_match_data_requires_both_readiness_checks(
    has_crown_odds, has_visible_odds
):
    page = FakePage()
    page.evaluate_result = {
        "hasCrownOdds": has_crown_odds,
        "hasVisibleOdds": has_visible_odds,
    }

    assert main.has_valid_match_data(page) is False


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
    monkeypatch.setattr(main, "BOT_TOKEN", "test-token")
    monkeypatch.setattr(main, "CHANNEL_ID", "test-channel")
    monkeypatch.setattr(main, "BROWSER_PROXY_SERVER", "socks5://proxy.test:1080")
    monkeypatch.setattr(main, "init_storage", lambda: True)
    monkeypatch.setattr(main, "sync_playwright", lambda: FakeSyncPlaywright(playwright))
    monkeypatch.setattr(main, "init_browser", lambda p, **kwargs: (browser, page))
    monkeypatch.setattr(main, "load_state_from_json", lambda: saved_state)
    monkeypatch.setattr(main, "switch_to_live", lambda page: None)
    monkeypatch.setattr(main.time, "monotonic", lambda: 100)
    return page, browser


@pytest.mark.parametrize(
    ("setting", "value"),
    [
        ("BOT_TOKEN", ""),
        ("CHANNEL_ID", "  "),
        ("BROWSER_PROXY_SERVER", None),
    ],
)
def test_main_fails_before_startup_when_required_configuration_is_missing(
    monkeypatch, setting, value
):
    configure_main(monkeypatch)
    monkeypatch.setattr(main, setting, value)
    storage_calls = []
    signal_calls = []
    monkeypatch.setattr(main, "init_storage", lambda: storage_calls.append(True))
    monkeypatch.setattr(main.signal, "signal", lambda *args: signal_calls.append(args))

    with pytest.raises(RuntimeError, match=f"{setting}"):
        main.main()

    assert storage_calls == []
    assert signal_calls == []


def test_main_restarts_after_prepare_page_failure(monkeypatch):
    page, browser = configure_main(monkeypatch)
    calls = []
    monkeypatch.setattr(
        main,
        "switch_to_live",
        lambda current_page: (_ for _ in ()).throw(RuntimeError("switch failed")),
    )
    def restart(current_browser):
        calls.append(current_browser)
        raise StopMain

    monkeypatch.setattr(main, "_restart_script", restart)

    with pytest.raises(StopMain):
        main.main()

    assert calls == [browser]


def test_main_starts_monitor_with_saved_state(monkeypatch):
    saved_state = {"active_match_ids": ["1"]}
    page, browser = configure_main(monkeypatch, saved_state=saved_state)
    calls = []
    monkeypatch.setattr(
        main, "collect_matches", lambda current_page: ["2"]
    )

    def stop_after_monitor(page, matches, **kwargs):
        calls.append((page, matches, kwargs))
        raise StopMain

    monkeypatch.setattr(main, "parse_and_monitor_match", stop_after_monitor)

    with pytest.raises(StopMain):
        main.main()

    assert calls == [
        (
            page,
            ["2"],
            {
                "saved_state": saved_state,
                "restart_deadline": 100 + main.RESTART_HOURS * 3600,
                "live_refresh_callback": main.refresh_live_table,
                "page_refresh_callback": main.refresh_page,
                "shutdown_requested": calls[0][2]["shutdown_requested"],
            },
        )
    ]
    assert isinstance(calls[0][2]["shutdown_requested"], main.threading.Event)
    assert browser.close_calls == 1


def test_main_waits_for_saved_match_and_refreshes_page(monkeypatch):
    saved_state = {"active_match_ids": ["1"]}
    page, _ = configure_main(monkeypatch, saved_state=saved_state)
    matches = iter([[], ["2"]])
    sleeps = []
    refreshes = []
    calls = []
    monkeypatch.setattr(main, "collect_matches", lambda current: next(matches))
    monkeypatch.setattr(main.time, "sleep", sleeps.append)
    monkeypatch.setattr(
        main, "_wait_for_shutdown", lambda event, seconds: sleeps.append(seconds)
    )
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
    assert calls == [
        (
            (page, ["2"]),
            {
                "saved_state": saved_state,
                "restart_deadline": 100 + main.RESTART_HOURS * 3600,
                "live_refresh_callback": main.refresh_live_table,
                "page_refresh_callback": main.refresh_page,
                "shutdown_requested": calls[0][1]["shutdown_requested"],
            },
        )
    ]


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
                "saved_state": None,
                "restart_deadline": 100 + main.RESTART_HOURS * 3600,
                "live_refresh_callback": main.refresh_live_table,
                "page_refresh_callback": main.refresh_page,
                "shutdown_requested": calls[0][1]["shutdown_requested"],
            },
        )
    ]
    assert isinstance(calls[0][1]["shutdown_requested"], main.threading.Event)


def test_main_waits_and_refreshes_until_matches_appear(monkeypatch):
    page, _ = configure_main(monkeypatch)
    observed_matches = iter([[], ["match-1"]])
    sleeps = []
    refreshes = []
    calls = []
    monkeypatch.setattr(main, "collect_matches", lambda current: next(observed_matches))
    monkeypatch.setattr(main.time, "sleep", sleeps.append)
    monkeypatch.setattr(
        main, "_wait_for_shutdown", lambda event, seconds: sleeps.append(seconds)
    )
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


def test_main_retries_no_match_page_refresh_then_restarts_without_loading_state(
    monkeypatch,
):
    page, browser = configure_main(monkeypatch)
    waits = []
    refresh_attempts = []
    sleeps = []
    state_loads = []
    restart_calls = []
    monkeypatch.setattr(main, "collect_matches", lambda current: [])
    monkeypatch.setattr(
        main,
        "_wait_for_shutdown",
        lambda event, seconds: waits.append(seconds),
    )
    monkeypatch.setattr(main.time, "sleep", sleeps.append)
    monkeypatch.setattr(
        main,
        "refresh_page",
        lambda current, wait_for_data: (
            refresh_attempts.append((current, wait_for_data)),
            (_ for _ in ()).throw(RuntimeError("reload failed")),
        )[1],
    )
    monkeypatch.setattr(
        main,
        "load_state_from_json",
        lambda: state_loads.append(True),
    )

    def restart(current_browser):
        restart_calls.append(current_browser)
        raise StopMain

    monkeypatch.setattr(main, "_restart_script", restart)

    with pytest.raises(StopMain):
        main.main()

    assert waits == [
        main.NO_MATCH_RETRY_DELAY_SECONDS,
        main.HARD_PAGE_REFRESH_RETRY_DELAY_SECONDS,
    ]
    assert refresh_attempts == [(page, False)] * 2
    assert sleeps == []
    assert state_loads == []
    assert restart_calls == [browser]


def test_main_stops_cleanly_when_shutdown_is_requested_without_matches(
    monkeypatch,
):
    page, browser = configure_main(monkeypatch)
    loads = []
    monitor_calls = []
    monkeypatch.setattr(main, "collect_matches", lambda current: [])
    monkeypatch.setattr(
        main, "load_state_from_json", lambda: loads.append(True)
    )
    monkeypatch.setattr(
        main,
        "parse_and_monitor_match",
        lambda *args, **kwargs: monitor_calls.append((args, kwargs)),
    )

    def request_shutdown(event, seconds):
        assert seconds == main.NO_MATCH_RETRY_DELAY_SECONDS
        event.set()
        return True

    monkeypatch.setattr(main, "_wait_for_shutdown", request_shutdown)

    main.main()

    assert loads == []
    assert monitor_calls == []
    assert browser.close_calls == 1
    assert page.reload_calls == []


def test_main_does_not_log_browser_close_failure_as_error_during_shutdown(
    monkeypatch, caplog
):
    class DisconnectedBrowser(FakeBrowser):
        def close(self):
            self.close_calls += 1
            raise RuntimeError("Connection closed while reading from the driver")

    browser = DisconnectedBrowser()
    configure_main(monkeypatch, browser=browser)
    monkeypatch.setattr(main, "collect_matches", lambda current: [])

    def request_shutdown(event, seconds):
        event.set()
        return True

    monkeypatch.setattr(main, "_wait_for_shutdown", request_shutdown)

    main.main()

    assert browser.close_calls == 1
    assert "Failed to close browser" not in caplog.text
    assert "already closed during shutdown" in caplog.text


def test_main_passes_shutdown_event_to_monitor_and_closes_browser(monkeypatch):
    _, browser = configure_main(monkeypatch)
    events = []
    monkeypatch.setattr(main, "collect_matches", lambda current: ["1"])

    def stop_monitor(page, matches, **kwargs):
        events.append(kwargs["shutdown_requested"])
        kwargs["shutdown_requested"].set()

    monkeypatch.setattr(main, "parse_and_monitor_match", stop_monitor)

    main.main()

    assert len(events) == 1
    assert events[0].is_set()
    assert browser.close_calls == 1


def test_main_registers_and_restores_termination_signal_handlers(monkeypatch):
    _, browser = configure_main(monkeypatch)
    previous_handlers = {
        main.signal.SIGINT: object(),
        main.signal.SIGTERM: object(),
    }
    registered_handlers = {}
    signal_calls = []

    def register_handler(signum, handler):
        signal_calls.append((signum, handler))
        if len(signal_calls) <= 2:
            registered_handlers[signum] = handler
        return previous_handlers[signum]

    monkeypatch.setattr(main.signal, "signal", register_handler)
    monkeypatch.setattr(main, "collect_matches", lambda current: ["1"])
    events = []

    def stop_monitor(page, matches, **kwargs):
        event = kwargs["shutdown_requested"]
        events.append(event)
        registered_handlers[main.signal.SIGTERM](main.signal.SIGTERM, None)

    monkeypatch.setattr(main, "parse_and_monitor_match", stop_monitor)

    main.main()

    assert events[0].is_set()
    assert [signum for signum, _ in signal_calls] == [
        main.signal.SIGINT,
        main.signal.SIGTERM,
        main.signal.SIGINT,
        main.signal.SIGTERM,
    ]
    assert signal_calls[-2][1] is previous_handlers[main.signal.SIGINT]
    assert signal_calls[-1][1] is previous_handlers[main.signal.SIGTERM]
    assert browser.close_calls == 1


def test_main_stops_if_shutdown_is_requested_during_browser_setup(
    monkeypatch, caplog
):
    configure_main(monkeypatch)

    def stop_during_setup(playwright, *, shutdown_requested):
        shutdown_requested.set()
        raise main.PageRestartRequired("Shutdown requested during load page")

    monkeypatch.setattr(main, "init_browser", stop_during_setup)

    main.main()

    assert "Shutdown requested during browser setup" in caplog.text


def test_main_stops_before_browser_start_if_shutdown_arrives_during_storage(
    monkeypatch,
):
    page, browser = configure_main(monkeypatch)
    registered_handlers = {}
    storage_calls = []
    browser_calls = []

    def register_handler(signum, handler):
        registered_handlers[signum] = handler
        return None

    def init_storage():
        storage_calls.append(True)
        registered_handlers[main.signal.SIGINT](main.signal.SIGINT, None)

    monkeypatch.setattr(main.signal, "signal", register_handler)
    monkeypatch.setattr(main, "init_storage", init_storage)
    monkeypatch.setattr(
        main,
        "init_browser",
        lambda *args, **kwargs: browser_calls.append(True),
    )

    main.main()

    assert storage_calls == [True]
    assert browser_calls == []
    assert page.goto_calls == []
    assert browser.close_calls == 0


def test_request_shutdown_sets_event():
    event = main.threading.Event()

    main._request_shutdown(event, main.signal.SIGINT, None)

    assert event.is_set()


def test_main_restarts_after_24_hours_even_without_matches(monkeypatch):
    _, browser = configure_main(monkeypatch)
    ticks = iter([0, 0, 0, 0, 24 * 60 * 60])
    monkeypatch.setattr(main.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(main, "collect_matches", lambda page: [])
    monkeypatch.setattr(main.sys, "executable", "python")
    monkeypatch.setattr(main.sys, "argv", ["main.py"])
    monkeypatch.setattr(
        main.os,
        "execv",
        lambda *args: (_ for _ in ()).throw(RuntimeError("exec called")),
    )

    with pytest.raises(RuntimeError, match="exec called"):
        main.main()

    assert browser.close_calls == 1


def test_restart_if_due_without_browser_restarts_script(monkeypatch):
    calls = []

    class StopRestart(Exception):
        pass

    monkeypatch.setattr(main.time, "monotonic", lambda: 100)
    monkeypatch.setattr(
        main,
        "_restart_script",
        lambda *args: (calls.append(args), (_ for _ in ()).throw(StopRestart))[1],
    )

    with pytest.raises(StopRestart):
        main._restart_if_due(99, None)

    assert calls == [()]


def test_main_restarts_process_on_page_restart_required(monkeypatch, caplog):
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
        main, "collect_match_status",
        lambda *args, **kwargs: [{"active": True, "match_id": "1"}],
    )
    monkeypatch.setattr(main.os, "execv", restart)
    monkeypatch.setattr(main.sys, "executable", "python.exe")
    monkeypatch.setattr(main.sys, "argv", ["main.py", "--flag"])

    with pytest.raises(RuntimeError, match="exec called"):
        main.main()

    assert browser.close_calls == 1
    assert exec_calls == [("python.exe", ["python.exe", "main.py", "--flag"])]
    restart_log = next(
        record for record in caplog.records
        if record.message == "restart. Restarting script..."
    )
    assert restart_log.levelname == "INFO"


def test_main_handles_scheduled_restart_without_string_matching(monkeypatch, caplog):
    _, browser = configure_main(
        monkeypatch, saved_state={"active_match_ids": ["1"]}
    )
    monkeypatch.setattr(
        main,
        "parse_and_monitor_match",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            main.ScheduledRestartRequired("Scheduled restart after 24 hours")
        ),
    )
    monkeypatch.setattr(
        main,
        "collect_match_status",
        lambda *args, **kwargs: [{"active": True, "match_id": "1"}],
    )
    monkeypatch.setattr(
        main.os, "execv", lambda *args: (_ for _ in ()).throw(RuntimeError("exec"))
    )

    with pytest.raises(RuntimeError, match="exec"):
        main.main()

    assert browser.close_calls == 1
    assert not any(
        record.message == "Scheduled restart after 24 hours. Restarting script..."
        for record in caplog.records
    )


def test_main_closes_browser_and_reraises_unexpected_failure(monkeypatch):
    _, browser = configure_main(
        monkeypatch, saved_state={"active_match_ids": ["1"]}
    )
    monkeypatch.setattr(
        main, "collect_match_status",
        lambda *args, **kwargs: [{"active": True, "match_id": "1"}],
    )
    monkeypatch.setattr(
        main,
        "parse_and_monitor_match",
        lambda *args, **kwargs: (_ for _ in ()).throw(ValueError("monitor failed")),
    )

    with pytest.raises(ValueError, match="monitor failed"):
        main.main()

    assert browser.close_calls == 1


def test_main_preserves_unexpected_failure_when_browser_close_fails(monkeypatch):
    class BrokenBrowser(FakeBrowser):
        def close(self):
            self.close_calls += 1
            raise RuntimeError("close failed")

    page = FakePage()
    browser = BrokenBrowser(context=FakeContext(page=page))
    configure_main(monkeypatch, browser=browser)
    monkeypatch.setattr(main, "collect_matches", lambda current_page: ["1"])
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
        main, "collect_match_status",
        lambda *args, **kwargs: [{"active": True, "match_id": "1"}],
    )
    monkeypatch.setattr(
        main.os, "execv", lambda *args: (_ for _ in ()).throw(RuntimeError("exec"))
    )

    with pytest.raises(RuntimeError, match="exec"):
        main.main()

    assert browser.close_calls == 1
