import json
from datetime import datetime, timezone

import pytest
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

import observer


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


def test_install_live_change_observer_evaluates_install_script():
    page = FakePage()

    observer._install_live_change_observer(page)

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
        observer._install_live_change_observer(page)


def test_remove_live_change_observer_evaluates_removal_script():
    page = FakePage()

    observer._remove_live_change_observer(page)

    script, args = page.evaluate_calls[0]
    assert not args
    assert "disconnect()" in script
    assert "delete window[observerKey]" in script
    assert "delete window[changeFlag]" in script


def test_remove_live_change_observer_logs_and_swallows_page_error(caplog):
    page = FakePage(evaluate_error=RuntimeError("page closed"))

    observer._remove_live_change_observer(page)

    assert "Could not stop live table observer" in caplog.text


def test_wait_for_live_change_resets_flag_and_returns_true():
    page = FakePage()

    assert observer._wait_for_live_change(page, 2500) is True
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

    assert observer._wait_for_live_change(page, 10) is False
    assert page.evaluate_calls == []


def test_wait_for_live_change_propagates_non_timeout_errors():
    page = FakePage(function_error=RuntimeError("execution context destroyed"))

    with pytest.raises(RuntimeError, match="execution context destroyed"):
        observer._wait_for_live_change(page, 10)


@pytest.mark.parametrize(
    ("active", "ready", "expected"),
    [
        ([{"active": True}], {"hasCrownOdds": True, "hasVisibleOdds": True}, True),
        ([{"active": False}], {"hasCrownOdds": True, "hasVisibleOdds": True}, False),
        ([{"active": True}], {"hasCrownOdds": False, "hasVisibleOdds": True}, None),
        ([{"active": True}], {"hasCrownOdds": True, "hasVisibleOdds": False}, None),
    ],
)
def test_reload_page_with_retries_handles_active_and_readiness_states(
    monkeypatch, active, ready, expected
):
    page = FakePage(evaluate_result=ready)
    refreshes = []
    sleeps = []
    monkeypatch.setattr(observer, "_collect_match_status", lambda *args, **kwargs: active)
    monkeypatch.setattr(observer.time, "sleep", sleeps.append)

    result = observer._reload_page_with_retries(
        page, lambda page: refreshes.append(page), max_retries=1
    )

    assert result is expected
    assert refreshes == [page]
    assert sleeps == []
    if active and active[0]["active"]:
        assert len(page.evaluate_calls) == 1
        assert "hasCrownOdds" in page.evaluate_calls[0][0]
    else:
        assert page.evaluate_calls == []


@pytest.mark.parametrize(
    ("status_error", "evaluate_error", "max_retries", "expected", "expected_sleeps"),
    [
        (RuntimeError("status failed"), None, 3, None, [2, 2]),
        (None, RuntimeError("page failed"), 2, None, [2]),
    ],
)
def test_reload_page_with_retries_retries_errors_then_returns_none(
    monkeypatch,
    status_error,
    evaluate_error,
    max_retries,
    expected,
    expected_sleeps,
    caplog,
):
    page = FakePage(
        evaluate_error=evaluate_error,
        evaluate_result={"hasCrownOdds": True, "hasVisibleOdds": True},
    )
    attempts = []
    sleeps = []

    def collect(*args, **kwargs):
        if status_error:
            raise status_error
        return [{"active": True}]

    monkeypatch.setattr(observer, "_collect_match_status", collect)
    monkeypatch.setattr(observer.time, "sleep", sleeps.append)

    result = observer._reload_page_with_retries(
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

    assert observer._reload_page_with_retries(page, lambda page: None, max_retries=0) is None
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

    assert observer._collect_match_status(page, require_crown) == expected
    script, args = page.evaluate_calls[0]
    assert "table#table_live tbody tr.tds" in script
    assert args == (require_crown,)


def test_collect_match_ids_filters_status_to_active_ids(monkeypatch):
    page = FakePage()
    monkeypatch.setattr(
        observer,
        "_collect_match_status",
        lambda received_page: [
            {"match_id": "10", "active": True},
            {"match_id": "20", "active": False},
            {"match_id": "30", "active": True},
        ],
    )

    assert observer._collect_match_ids(page) == ["10", "30"]


@pytest.mark.parametrize(
    ("debug_mode", "expected_file"),
    [(0, False), (1, True), (2, False)],
)
def test_write_match_count_respects_debug_mode(
    tmp_path, monkeypatch, debug_mode, expected_file
):
    monkeypatch.setattr(observer, "DEBUGMODE", debug_mode)
    monkeypatch.setattr(observer, "__file__", str(tmp_path / "observer.py"))
    records = [
        {"match_id": 10, "label": "Home - Away"},
        {"match_id": "20", "label": "One - Two"},
        {"match_id": None, "label": "Unknown - Unknown"},
    ]

    observer._write_match_count(records, [10])

    snapshot = tmp_path / "count.json"
    assert snapshot.exists() is expected_file
    if expected_file:
        assert json.loads(snapshot.read_text(encoding="utf-8")) == {
            "Active": ["Home - Away"],
            "Inactive": ["One - Two", "Unknown - Unknown"],
        }


def test_write_match_count_logs_snapshot_write_failure(monkeypatch, caplog):
    monkeypatch.setattr(observer, "DEBUGMODE", 1)

    def fail_open(*args, **kwargs):
        raise OSError("permission denied")

    monkeypatch.setattr("builtins.open", fail_open)

    observer._write_match_count([{"match_id": "1", "label": "A - B"}], [])

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

    result = observer._extract_all_match_data(page, ["1", "2"])

    assert result is returned
    script, args = page.evaluate_calls[0]
    assert "document.getElementById('tr1_' + match_id)" in script
    assert args == (["1", "2"],)
    assert result["1"]["date"] == now.astimezone().isoformat(timespec="seconds")
    assert result["2"] is None


def test_extract_all_match_data_logs_and_reraises_page_error(caplog):
    page = FakePage(evaluate_error=RuntimeError("page unavailable"))

    with pytest.raises(RuntimeError, match="page unavailable"):
        observer._extract_all_match_data(page, ["1"])

    assert "Error in _extract_all_match_data: page unavailable" in caplog.text
