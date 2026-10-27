import json
import threading
from datetime import datetime, timezone

import pytest
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from app import observer


def test_observer_exports_shared_page_operations():
    assert observer.__all__ == [
        "PageRestartRequired",
        "ScheduledRestartRequired",
        "page_is_closed",
        "install_live_change_observer",
        "remove_live_change_observer",
        "wait_for_live_change",
        "reload_page_with_retries",
        "collect_match_status",
        "write_match_count",
        "extract_all_match_data",
    ]


def test_observer_logger_uses_observer_module_name():
    assert observer.logger.name == observer.__name__


class FakePage:
    def __init__(self, *, evaluate_result=None, evaluate_error=None, function_error=None):
        self.evaluate_result = evaluate_result
        self.evaluate_error = evaluate_error
        self.function_error = function_error
        self.evaluate_calls = []
        self.function_calls = []

    def evaluate(self, script, *args):
        self.evaluate_calls.append((script, args))
        if self.evaluate_error:
            raise self.evaluate_error
        if callable(self.evaluate_result):
            return self.evaluate_result(script, *args)
        return self.evaluate_result

    def wait_for_function(self, *args, **kwargs):
        self.function_calls.append((args, kwargs))
        if self.function_error:
            raise self.function_error


def test_page_restart_required_is_a_dedicated_exception():
    error = observer.PageRestartRequired("restart")

    assert isinstance(error, Exception)
    assert str(error) == "restart"


@pytest.mark.parametrize("is_closed", [True, False])
def test_page_is_closed_uses_playwright_page_state(is_closed):
    class ClosedStatePage:
        def is_closed(self):
            return is_closed

    assert observer.page_is_closed(
        ClosedStatePage(), PlaywrightError("unrelated wording")
    ) is is_closed


def test_page_is_closed_uses_error_text_as_fallback():
    class PageWithoutClosedState:
        pass

    assert observer.page_is_closed(
        PageWithoutClosedState(),
        RuntimeError("Target page has been closed"),
    )
    assert observer.page_is_closed(
        type("OpenPage", (), {"is_closed": lambda self: False})(),
        PlaywrightError("Target page has been closed"),
    )


def test_install_live_change_observer_evaluates_install_script():
    page = FakePage()

    observer.install_live_change_observer(page)

    assert len(page.evaluate_calls) == 1
    script, args = page.evaluate_calls[0]
    assert not args
    assert "__fixi_live_table_observer" in script
    assert "__fixi_live_table_changed" in script
    assert "Live table not found." in script
    assert "new MutationObserver" in script


def test_install_live_change_observer_propagates_evaluation_error():
    page = FakePage(evaluate_error=RuntimeError("page closed"))

    with pytest.raises(RuntimeError, match="page closed"):
        observer.install_live_change_observer(page)


def test_remove_live_change_observer_evaluates_removal_script():
    page = FakePage()

    observer.remove_live_change_observer(page)

    script, args = page.evaluate_calls[0]
    assert not args
    assert "disconnect()" in script
    assert "delete window[observerKey]" in script
    assert "delete window[changeFlag]" in script


def test_remove_live_change_observer_logs_and_swallows_page_error(caplog):
    page = FakePage(evaluate_error=RuntimeError("page closed"))

    observer.remove_live_change_observer(page)

    assert "Could not stop live table observer" in caplog.text


def test_wait_for_live_change_resets_flag_and_returns_true():
    page = FakePage()

    assert observer.wait_for_live_change(page, 2500) is True
    assert page.function_calls == [
        (
            ("() => window.__fixi_live_table_changed === true",),
            {"timeout": 2500, "polling": 100},
        )
    ]
    assert page.evaluate_calls == [
        ("window.__fixi_live_table_changed = false", ())
    ]


def test_wait_for_live_change_returns_false_on_playwright_timeout():
    page = FakePage(function_error=PlaywrightTimeoutError("timed out"))

    assert observer.wait_for_live_change(page, 10) is False
    assert page.evaluate_calls == []


def test_wait_for_live_change_stops_when_shutdown_is_requested():
    shutdown_requested = threading.Event()

    class ShutdownPage(FakePage):
        def wait_for_function(self, *args, **kwargs):
            self.function_calls.append((args, kwargs))
            shutdown_requested.set()
            raise PlaywrightTimeoutError("wait interrupted")

    page = ShutdownPage()

    assert observer.wait_for_live_change(
        page, 10_000, shutdown_requested=shutdown_requested
    ) is False
    assert len(page.function_calls) == 1
    assert page.function_calls[0][1]["timeout"] <= 1000
    assert page.evaluate_calls == []


def test_wait_for_live_change_propagates_non_timeout_errors():
    page = FakePage(function_error=RuntimeError("execution context destroyed"))

    with pytest.raises(RuntimeError, match="execution context destroyed"):
        observer.wait_for_live_change(page, 10)


@pytest.mark.parametrize(
    ("active", "expected"),
    [
        ([{"active": True}], True),
        ([{"active": False}], False),
        ([{"active": True}, {"active": False}], True),
        ([], False),
    ],
)
def test_reload_page_with_retries_handles_active_and_readiness_states(
    monkeypatch, active, expected
):
    page = FakePage()
    refreshes = []
    sleeps = []
    monkeypatch.setattr(observer, "collect_match_status", lambda *args, **kwargs: active)
    monkeypatch.setattr(observer.time, "sleep", sleeps.append)

    result = observer.reload_page_with_retries(
        page, lambda page: refreshes.append(page), max_retries=1
    )

    assert result is expected
    assert refreshes == [page]
    assert sleeps == []


@pytest.mark.parametrize(
    "active",
    [
        [{"active": False}],
        [],
    ],
)
def test_reload_page_with_retries_does_not_retry_when_matches_or_odds_are_missing(
    monkeypatch, active
):
    page = FakePage()
    refreshes = []
    sleeps = []
    monkeypatch.setattr(observer, "collect_match_status", lambda *args, **kwargs: active)
    monkeypatch.setattr(observer.time, "sleep", sleeps.append)

    result = observer.reload_page_with_retries(
        page,
        lambda current_page: refreshes.append(current_page),
        max_retries=3,
    )

    assert result is False
    assert refreshes == [page]
    assert sleeps == []


@pytest.mark.parametrize(
    ("status_error", "max_retries", "expected", "expected_sleeps"),
    [
        (
            RuntimeError("status failed"),
            observer.LIVE_REFRESH_MAX_RETRIES,
            None,
            [2] * (observer.LIVE_REFRESH_MAX_RETRIES - 1),
        ),
    ],
)
def test_reload_page_with_retries_retries_errors_then_returns_none(
    monkeypatch,
    status_error,
    max_retries,
    expected,
    expected_sleeps,
    caplog,
):
    page = FakePage()
    attempts = []
    sleeps = []

    def collect(*args, **kwargs):
        if status_error:
            raise status_error
        return [{"active": True}]

    monkeypatch.setattr(observer, "collect_match_status", collect)
    monkeypatch.setattr(observer.time, "sleep", sleeps.append)

    result = observer.reload_page_with_retries(
        page,
        lambda current_page: attempts.append(current_page),
        max_retries=max_retries,
        retry_delay=2,
    )

    assert result is expected
    assert attempts == [page] * max_retries
    assert sleeps == expected_sleeps
    assert "Live refresh failed after" in caplog.text


def test_reload_page_with_retries_zero_attempts_returns_none():
    page = FakePage()

    assert observer.reload_page_with_retries(page, lambda page: None, max_retries=0) is None


def test_reload_page_with_retries_stops_when_shutdown_is_already_requested():
    page = FakePage()
    shutdown_requested = threading.Event()
    shutdown_requested.set()
    refreshes = []

    with pytest.raises(observer.PageRestartRequired, match="Shutdown requested"):
        observer.reload_page_with_retries(
            page,
            lambda current_page: refreshes.append(current_page),
            shutdown_requested=shutdown_requested,
        )

    assert refreshes == []


def test_reload_page_with_retries_stops_after_refresh_is_interrupted():
    page = FakePage()
    shutdown_requested = threading.Event()
    refreshes = []

    def refresh(current_page):
        refreshes.append(current_page)
        shutdown_requested.set()
        raise RuntimeError("Target page has been closed")

    with pytest.raises(observer.PageRestartRequired, match="Shutdown requested"):
        observer.reload_page_with_retries(
            page,
            refresh,
            shutdown_requested=shutdown_requested,
        )

    assert refreshes == [page]


def test_reload_page_with_retries_stops_when_shutdown_interrupts_retry_wait(
    monkeypatch,
):
    page = FakePage()
    shutdown_requested = threading.Event()
    refreshes = []
    waits = []

    def wait(timeout):
        waits.append(timeout)
        shutdown_requested.set()
        return True

    monkeypatch.setattr(shutdown_requested, "wait", wait)

    def refresh(current_page):
        refreshes.append(current_page)
        raise RuntimeError("refresh failed")

    with pytest.raises(observer.PageRestartRequired, match="Shutdown requested"):
        observer.reload_page_with_retries(
            page,
            refresh,
            retry_delay=5,
            shutdown_requested=shutdown_requested,
        )

    assert refreshes == [page]
    assert waits == [5]
    assert page.evaluate_calls == []


@pytest.mark.parametrize(
    ("require_crown", "expected"),
    [
        (False, [{"match_id": "1", "label": "A - B", "active": True}]),
        (True, []),
    ],
)
def test_collect_match_status_passes_require_crown_and_returns_page_result(
    require_crown, expected
):
    page = FakePage(evaluate_result=expected)

    assert observer.collect_match_status(page, require_crown) == expected
    script, args = page.evaluate_calls[0]
    assert "table#table_live tbody tr.tds" in script
    assert args == (require_crown,)


@pytest.mark.parametrize(
    ("debug_mode", "expected_file"),
    [(0, False), (1, True), (2, False)],
)
def test_write_match_count_respects_debug_mode(
    tmp_path, monkeypatch, debug_mode, expected_file
):
    monkeypatch.setattr(observer, "DEBUGMODE", debug_mode)
    monkeypatch.setattr(observer, "DATA_DIR", tmp_path / "data")
    records = [
        {"match_id": 10, "label": "Home - Away"},
        {"match_id": "20", "label": "One - Two"},
        {"match_id": None, "label": "Unknown - Unknown"},
    ]

    observer.write_match_count(records, [10])

    snapshot = tmp_path / "data" / "count.json"
    assert snapshot.exists() is expected_file
    if expected_file:
        assert json.loads(snapshot.read_text(encoding="utf-8")) == {
            "Active": ["Home - Away"],
            "Inactive": ["One - Two", "Unknown - Unknown"],
        }


def test_write_match_count_logs_snapshot_write_failure(monkeypatch, tmp_path, caplog):
    monkeypatch.setattr(observer, "DEBUGMODE", 1)
    monkeypatch.setattr(observer, "DATA_DIR", tmp_path / "data")

    def fail_open(*args, **kwargs):
        raise OSError("permission denied")

    monkeypatch.setattr("builtins.open", fail_open)

    observer.write_match_count([{"match_id": "1", "label": "A - B"}], [])

    assert "Failed to write match count snapshot" in caplog.text


def test_extract_all_match_data_passes_ids_and_adds_local_timestamp(monkeypatch):
    returned = {
        "1": {"team1": "Home", "ov": {"over": "2.5"}},
        "2": None,
    }
    now = datetime(2026, 10, 9, 12, 34, 56, tzinfo=timezone.utc)
    monkeypatch.setattr(observer, "datetime", type("FixedDatetime", (), {
        "now": staticmethod(lambda: now)
    }))
    page = FakePage(evaluate_result=returned)

    result = observer.extract_all_match_data(page, ["1", "2"])

    assert result is returned
    script, args = page.evaluate_calls[0]
    assert "document.getElementById('tr1_' + match_id)" in script
    assert args == (["1", "2"],)
    assert result["1"]["date"] == now.astimezone().isoformat(timespec="seconds")
    assert result["2"] is None


def test_extract_all_match_data_logs_and_reraises_page_error(caplog):
    page = FakePage(evaluate_error=RuntimeError("page unavailable"))

    with pytest.raises(RuntimeError, match="page unavailable"):
        observer.extract_all_match_data(page, ["1"])

    assert "Error in extract_all_match_data: page unavailable" in caplog.text
