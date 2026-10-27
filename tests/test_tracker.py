import json
import threading

import pytest

from app import tracker


class FakePage:
    pass


@pytest.fixture
def monitor():
    return tracker.MatchMonitor(
        FakePage(),
        live_refresh_callback=lambda page: None,
    )


def market_data(*, ah=None, over=None, match_time="12", score="0-0"):
    return {
        "ah": ah if ah is not None else {"ah": "0", "home_ah_odds": "1.9"},
        "ov": over if over is not None else {"over": "2.5", "over_odds": "1.9"},
        "match_time": match_time,
        "score": score,
    }


def test_match_monitor_initializes_runtime_state_and_callbacks():
    page = FakePage()
    live_callback = lambda current_page: None
    page_callback = lambda current_page, wait_for_data=True: None

    result = tracker.MatchMonitor(
        page,
        ["1"],
        {"active_match_ids": ["2"]},
        live_refresh_callback=live_callback,
        page_refresh_callback=page_callback,
    )

    assert result.page is page
    assert result.match_ids == ["1"]
    assert result.saved_state == {"active_match_ids": ["2"]}
    assert result.live_refresh_callback is live_callback
    assert result.page_refresh_callback is page_callback
    assert result.match_history == {}
    assert result.last_data == {}
    assert result.pending_notifications == {}
    assert result.active_match_ids == []


def test_run_installs_and_removes_observer_and_propagates_restart(monkeypatch, monitor):
    events = []
    restart = tracker.PageRestartRequired("restart")
    monkeypatch.setattr(monitor, "_init_or_restore_state", lambda: events.append("init"))
    monkeypatch.setattr(tracker, "install_live_change_observer", lambda page: events.append("install"))
    monkeypatch.setattr(monitor, "_monitor_loop", lambda: (_ for _ in ()).throw(restart))
    monkeypatch.setattr(tracker, "remove_live_change_observer", lambda page: events.append("remove"))

    with pytest.raises(tracker.PageRestartRequired, match="restart"):
        monitor.run()

    assert events == ["init", "install", "remove"]


def test_run_logs_monitoring_started_after_match_synchronization(
    monkeypatch, monitor
):
    messages = []
    restart = tracker.PageRestartRequired("restart")

    def initialize():
        messages.append("Matches synchronized: Active 45 New 45 Removed 0")

    monkeypatch.setattr(monitor, "_init_or_restore_state", initialize)
    monkeypatch.setattr(tracker.logger, "info", messages.append)
    monkeypatch.setattr(tracker, "install_live_change_observer", lambda page: None)
    monkeypatch.setattr(monitor, "_monitor_loop", lambda: (_ for _ in ()).throw(restart))
    monkeypatch.setattr(tracker, "remove_live_change_observer", lambda page: None)

    with pytest.raises(tracker.PageRestartRequired, match="restart"):
        monitor.run()

    assert messages == [
        "Matches synchronized: Active 45 New 45 Removed 0",
        "Monitoring started",
    ]


def test_run_saves_state_and_wraps_unexpected_failure(monkeypatch, monitor, tmp_path):
    events = []
    monkeypatch.setattr(monitor, "_init_or_restore_state", lambda: setattr(monitor, "active_match_ids", ["1"]))
    monkeypatch.setattr(tracker, "install_live_change_observer", lambda page: None)
    monkeypatch.setattr(monitor, "_monitor_loop", lambda: (_ for _ in ()).throw(ValueError("broken")))
    monkeypatch.setattr(tracker, "remove_live_change_observer", lambda page: events.append("removed"))
    monkeypatch.setattr(
        monitor,
        "_save_state_to_json",
        lambda active, data: events.append((active, data)) or True,
    )

    with pytest.raises(tracker.PageRestartRequired, match="Monitoring failed") as error:
        monitor.run()

    assert isinstance(error.value.__cause__, ValueError)
    assert events == ["removed", (["1"], {})]


def test_run_logs_when_state_cannot_be_saved_before_restart(
    monkeypatch, monitor, caplog
):
    monkeypatch.setattr(monitor, "_init_or_restore_state", lambda: None)
    monkeypatch.setattr(tracker, "install_live_change_observer", lambda page: None)
    monkeypatch.setattr(monitor, "_monitor_loop", lambda: (_ for _ in ()).throw(ValueError("broken")))
    monkeypatch.setattr(tracker, "remove_live_change_observer", lambda page: None)
    monkeypatch.setattr(monitor, "_save_state_to_json", lambda *args: False)

    with pytest.raises(tracker.PageRestartRequired):
        monitor.run()

    assert "Could not save monitoring state before restart" in caplog.text


def test_run_saves_current_match_state_on_graceful_shutdown(
    monkeypatch, tmp_path
):
    page = FakePage()
    shutdown_requested = threading.Event()
    initial = market_data()
    monitor = tracker.MatchMonitor(
        page,
        ["1"],
        live_refresh_callback=lambda current_page: None,
        shutdown_requested=shutdown_requested,
    )
    state_path = tmp_path / "state.json"
    save_state = monitor._save_state_to_json
    monkeypatch.setattr(
        monitor,
        "_save_state_to_json",
        lambda active, last: save_state(active, last, state_path),
    )
    monkeypatch.setattr(
        tracker, "extract_all_match_data", lambda current_page, ids: {"1": initial}
    )
    monkeypatch.setattr(tracker, "install_live_change_observer", lambda page: None)
    removed = []
    monkeypatch.setattr(
        tracker, "remove_live_change_observer", lambda page: removed.append(page)
    )

    def request_shutdown(page, timeout_ms, shutdown_requested=None):
        assert shutdown_requested is monitor.shutdown_requested
        shutdown_requested.set()
        return False

    monkeypatch.setattr(tracker, "wait_for_live_change", request_shutdown)

    monitor.run()

    saved_state = json.loads(state_path.read_text(encoding="utf-8"))
    assert saved_state["active_match_ids"] == ["1"]
    assert saved_state["match_history"] == {
        "1": {"initial": initial, "changes": []}
    }
    assert saved_state["last_data"]["1"]["score"] == initial["score"]
    assert saved_state["pending_notifications"] == {}
    assert removed == [page]


def test_save_state_to_json_persists_all_state_and_returns_true(monitor, tmp_path):
    path = tmp_path / "state.json"
    monitor.match_history = {"1": {"initial": market_data(), "changes": []}}
    monitor.pending_notifications = {"20": {"match_id": "1"}}

    assert monitor._save_state_to_json(["1"], {"1": market_data()}, path)

    assert json.loads(path.read_text(encoding="utf-8")) == {
        "match_history": monitor.match_history,
        "last_data": {"1": market_data()},
        "active_match_ids": ["1"],
        "pending_notifications": monitor.pending_notifications,
    }


def test_save_state_to_json_logs_write_error_and_returns_false(
    monitor, tmp_path, monkeypatch, caplog
):
    def fail_open(*args, **kwargs):
        raise OSError("permission denied")

    monkeypatch.setattr("builtins.open", fail_open)

    assert not monitor._save_state_to_json([], {}, tmp_path / "state.json")
    assert "Failed to save state" in caplog.text


def test_init_or_restore_state_restores_state_and_backfills_dates(monitor):
    initial = market_data()
    changed = market_data(score="1-0")
    saved_state = {
        "active_match_ids": ["1"],
        "match_history": {
            "1": {"initial": initial, "changes": [changed]},
        },
        "last_data": {"1": market_data()},
        "pending_notifications": {"5": {"match_id": "1"}},
    }
    monitor.saved_state = saved_state
    monitor._run_analyzer = lambda: pytest.fail("analyzer should not run when restoring")

    monitor._init_or_restore_state()

    assert monitor.active_match_ids == ["1"]
    assert initial["date"] is None
    assert changed["date"] is None
    assert monitor.last_data["1"]["score"] == "0-0"
    assert monitor.pending_notifications == {"5": {"match_id": "1"}}


def test_init_or_restore_state_backfills_score_from_latest_change(monitor):
    initial = market_data(score="0-0")
    latest = market_data(score="2-1")
    monitor.saved_state = {
        "active_match_ids": ["1"],
        "match_history": {"1": {"initial": initial, "changes": [latest]}},
        "last_data": {"1": {"ah": initial["ah"], "ov": initial["ov"]}},
    }

    monitor._init_or_restore_state()

    assert monitor.last_data["1"]["score"] == "2-1"


def test_init_or_restore_state_syncs_saved_history_with_current_matches(
    monkeypatch, monitor
):
    existing_initial = market_data(score="0-0")
    new_initial = market_data(score="1-0")
    monitor.match_ids = ["stay", "new"]
    monitor.saved_state = {
        "active_match_ids": [],
        "match_history": {
            "stay": {"initial": existing_initial, "changes": []},
            "stale": {"initial": market_data(), "changes": []},
        },
        "last_data": {
            "stay": market_data(score="0-0"),
            "stale": market_data(),
        },
    }
    extracted = []
    monkeypatch.setattr(
        tracker,
        "extract_all_match_data",
        lambda page, ids: (
            extracted.append((page, ids)) or {"new": new_initial}
        ),
    )

    monitor._init_or_restore_state()

    assert extracted == [(monitor.page, ["new"])]
    assert monitor.active_match_ids == ["stay", "new"]
    assert set(monitor.match_history) == {"stay", "new"}
    assert monitor.match_history["stay"]["initial"] is existing_initial
    assert monitor.match_history["new"] == {
        "initial": new_initial,
        "changes": [],
    }
    assert "stale" not in monitor.last_data


def test_init_or_restore_state_initializes_matches_and_runs_analyzer(
    monkeypatch, monitor
):
    initialized = market_data()
    calls = []
    monkeypatch.setattr(
        tracker,
        "extract_all_match_data",
        lambda page, ids: calls.append((page, ids)) or {"1": initialized, "2": None},
    )
    monitor.match_ids = ["1", "2"]
    monkeypatch.setattr(monitor, "_run_analyzer", lambda: calls.append("analyze"))

    monitor._init_or_restore_state()

    assert calls == [(monitor.page, ["1", "2"]), "analyze"]
    assert monitor.match_history == {"1": {"initial": initialized, "changes": []}}
    assert monitor.last_data["1"] == {
        "ah": initialized["ah"],
        "ov": initialized["ov"],
        "match_time": "12",
        "score": "0-0",
    }


def test_run_analyzer_passes_full_history_to_pattern_matcher(
    monkeypatch, monitor
):
    initial = market_data()
    changed = market_data(score="1-0")
    changed_again = market_data(score="1-1")
    monitor.match_history = {
        "1": {
            "initial": initial,
            "changes": [changed, changed_again],
        }
    }
    calls = []
    monkeypatch.setattr(
        tracker, "find_pattern_matches", lambda history, callback: calls.append((history, callback))
    )

    monitor._run_analyzer()

    assert len(calls) == 1
    assert calls[0][0] is monitor.match_history
    assert calls[0][0]["1"]["changes"] == [changed, changed_again]
    assert calls[0][1].__self__ is monitor
    assert calls[0][1].__func__ is tracker.MatchMonitor._register_pending_notification


def test_run_analyzer_writes_debug_snapshot(monkeypatch, monitor, tmp_path):
    monkeypatch.setattr(tracker, "DEBUGMODE", 1)
    monkeypatch.setattr(tracker, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(tracker, "find_pattern_matches", lambda *args: None)
    monitor.match_history = {"1": {"initial": market_data(), "changes": []}}

    monitor._run_analyzer()

    snapshot = json.loads(
        (tmp_path / "data" / "data_closed_ov_ah.json").read_text(encoding="utf-8")
    )
    assert snapshot["1"]["initial"] == monitor.match_history["1"]["initial"]


def test_run_analyzer_logs_debug_snapshot_write_error(
    monkeypatch, monitor, tmp_path, caplog
):
    monkeypatch.setattr(tracker, "DEBUGMODE", 1)
    monkeypatch.setattr(tracker, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(tracker, "find_pattern_matches", lambda *args: None)
    monkeypatch.setattr(
        "builtins.open",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("permission denied")),
    )

    monitor._run_analyzer()

    assert "Failed to write analyzer input snapshot" in caplog.text


def test_load_initial_data_sets_history_and_last_data(monkeypatch, monitor):
    initial = market_data()
    monitor.active_match_ids = ["1", "2"]
    monkeypatch.setattr(
        tracker, "extract_all_match_data", lambda page, ids: {"1": initial, "2": None}
    )

    monitor._load_initial_data()

    assert monitor.match_history == {"1": {"initial": initial, "changes": []}}
    assert monitor.last_data["1"] == {
        "ah": initial["ah"],
        "ov": initial["ov"],
        "match_time": "12",
        "score": "0-0",
    }


def test_trigger_scheduled_restart_saves_and_raises(monkeypatch, monitor):
    saved = []
    monkeypatch.setattr(
        monitor, "_save_state_to_json", lambda *args: saved.append(args) or True
    )

    with pytest.raises(tracker.ScheduledRestartRequired, match="Scheduled restart"):
        monitor._trigger_scheduled_restart()

    assert saved == [(monitor.active_match_ids, monitor.last_data)]


def test_trigger_scheduled_restart_continues_when_state_save_fails(
    monkeypatch, monitor, caplog
):
    monkeypatch.setattr(monitor, "_save_state_to_json", lambda *args: False)

    with pytest.raises(tracker.ScheduledRestartRequired, match="Scheduled restart"):
        monitor._trigger_scheduled_restart()

    assert "proceeding with restart" in caplog.text


def test_monitor_loop_runs_scheduled_page_refresh_and_heartbeat(
    monkeypatch, monitor
):
    class StopLoop(Exception):
        pass

    monitor.page_refresh_callback = lambda page: None
    monitor.next_page_refresh_at = 0
    monitor.next_heartbeat_at = 0
    monitor.next_reload_at = 1000
    monitor.restart_deadline = 1000
    monkeypatch.setattr(tracker.time, "monotonic", lambda: 10)
    monkeypatch.setattr(tracker.time, "time", lambda: 0)
    monkeypatch.setattr(monitor, "_do_scheduled_page_refresh", lambda: True)
    monkeypatch.setattr(monitor, "_run_analyzer", lambda: None)
    processed = []

    def process_due():
        processed.append(True)
        if len(processed) == 3:
            raise StopLoop

    monkeypatch.setattr(monitor, "_process_due_notifications", process_due)

    with pytest.raises(StopLoop):
        monitor._monitor_loop()

    assert len(processed) == 3
    assert monitor.next_heartbeat_at == 110


def test_monitor_loop_reschedules_failed_scheduled_page_refresh(
    monkeypatch, monitor, caplog
):
    class StopLoop(Exception):
        pass

    monitor.page_refresh_callback = lambda page: None
    monitor.next_page_refresh_at = 0
    monitor.next_heartbeat_at = 1000
    monitor.next_reload_at = 1000
    monitor.restart_deadline = 1000
    monkeypatch.setattr(tracker.time, "monotonic", lambda: 10)
    monkeypatch.setattr(tracker.time, "time", lambda: 0)
    monkeypatch.setattr(
        monitor,
        "_do_scheduled_page_refresh",
        lambda: (_ for _ in ()).throw(RuntimeError("refresh failed")),
    )
    processed = []

    def process_due():
        processed.append(True)
        if len(processed) == 3:
            raise StopLoop

    monkeypatch.setattr(monitor, "_process_due_notifications", process_due)

    with pytest.raises(StopLoop):
        monitor._monitor_loop()

    assert monitor.next_page_refresh_at == (
        10 + tracker.PAGE_REFRESH_FAILURE_RETRY_DELAY_SECONDS
    )
    assert "Scheduled page refresh failed" in caplog.text


def test_monitor_loop_propagates_restart_from_scheduled_page_refresh(
    monkeypatch, monitor
):
    monitor.page_refresh_callback = lambda page: None
    monitor.next_page_refresh_at = 0
    monitor.next_reload_at = 1000
    monitor.next_heartbeat_at = 1000
    monitor.restart_deadline = 1000
    monkeypatch.setattr(tracker.time, "monotonic", lambda: 10)
    monkeypatch.setattr(tracker.time, "time", lambda: 0)
    monkeypatch.setattr(monitor, "_process_due_notifications", lambda: None)
    monkeypatch.setattr(
        monitor,
        "_do_scheduled_page_refresh",
        lambda: (_ for _ in ()).throw(tracker.PageRestartRequired("restart")),
    )

    with pytest.raises(tracker.PageRestartRequired, match="restart"):
        monitor._monitor_loop()


def test_monitor_loop_periodic_reload_polls_active_matches_and_analyzes(
    monkeypatch, monitor
):
    class StopLoop(Exception):
        pass

    monitor.next_reload_at = 0
    monitor.next_heartbeat_at = 1000
    monitor.restart_deadline = 1000
    monitor.active_match_ids = ["1"]
    monkeypatch.setattr(tracker.time, "monotonic", lambda: 10)
    monkeypatch.setattr(tracker.time, "time", lambda: 0)
    monkeypatch.setattr(monitor, "_do_periodic_reload", lambda: True)
    polls = []
    analyses = []
    monkeypatch.setattr(
        monitor, "_poll_and_update", lambda **kwargs: polls.append(kwargs) or (True, False)
    )
    monkeypatch.setattr(monitor, "_run_analyzer", lambda: analyses.append(True))
    processed = []

    def process_due():
        processed.append(True)
        if len(processed) == 3:
            raise StopLoop

    monkeypatch.setattr(monitor, "_process_due_notifications", process_due)

    with pytest.raises(StopLoop):
        monitor._monitor_loop()

    assert polls == [{"log_data_loaded": True}]
    assert analyses == [True]


def test_monitor_loop_polls_after_live_change_and_processes_no_change(
    monkeypatch, monitor
):
    class StopLoop(Exception):
        pass

    monitor.next_reload_at = 1000
    monitor.next_heartbeat_at = 1000
    monitor.restart_deadline = 1000
    monitor.active_match_ids = ["1"]
    monitor.pending_notifications = {"10": {"deadline": 5}}
    monkeypatch.setattr(tracker.time, "monotonic", lambda: 10)
    monkeypatch.setattr(tracker.time, "time", lambda: 0)
    changes = iter([True, False])
    monkeypatch.setattr(
        tracker,
        "wait_for_live_change",
        lambda *args, **kwargs: next(changes),
    )
    polls = []
    analyses = []
    monkeypatch.setattr(
        monitor, "_poll_and_update", lambda: polls.append(True) or (True, False)
    )
    monkeypatch.setattr(monitor, "_run_analyzer", lambda: analyses.append(True))
    processed = []

    def process_due():
        processed.append(True)
        if len(processed) == 4:
            raise StopLoop

    monkeypatch.setattr(monitor, "_process_due_notifications", process_due)

    with pytest.raises(StopLoop):
        monitor._monitor_loop()

    assert polls == [True]
    assert analyses == [True]
    assert len(processed) == 4


def test_monitor_loop_triggers_scheduled_restart(monkeypatch, monitor):
    monkeypatch.setattr(tracker.time, "monotonic", lambda: 10)
    monkeypatch.setattr(tracker.time, "time", lambda: 100)
    monitor.restart_deadline = 10
    monkeypatch.setattr(monitor, "_process_due_notifications", lambda: None)

    with pytest.raises(tracker.PageRestartRequired, match="Scheduled restart"):
        monitor._monitor_loop()


@pytest.mark.parametrize("refresh_kind", ["page", "table"])
def test_monitor_loop_restarts_after_refresh_crosses_deadline(
    monkeypatch, monitor, refresh_kind
):
    clock = [0]
    monkeypatch.setattr(tracker.time, "monotonic", lambda: clock[0])
    monitor.restart_deadline = 5
    monitor.next_reload_at = 0 if refresh_kind == "table" else 100
    monitor.next_page_refresh_at = 0 if refresh_kind == "page" else 100
    monitor.page_refresh_callback = lambda page: None
    monkeypatch.setattr(monitor, "_process_due_notifications", lambda: None)

    def finish_refresh(*args, **kwargs):
        clock[0] = 5
        return False

    refresh = (
        "_do_scheduled_page_refresh"
        if refresh_kind == "page"
        else "_do_periodic_reload"
    )
    monkeypatch.setattr(monitor, refresh, finish_refresh)

    with pytest.raises(tracker.PageRestartRequired, match="Scheduled restart"):
        monitor._monitor_loop()


def test_wait_for_matches_restarts_at_deadline_before_another_refresh(
    monkeypatch, monitor
):
    clock = [0]
    sleeps = []
    monkeypatch.setattr(tracker.time, "monotonic", lambda: clock[0])

    def sleep(seconds):
        sleeps.append(seconds)
        clock[0] += seconds

    monkeypatch.setattr(tracker.time, "sleep", sleep)
    monkeypatch.setattr(
        monitor,
        "_do_hard_page_refresh_with_retries",
        lambda: pytest.fail("must not refresh after restart deadline"),
    )
    monitor.restart_deadline = 5
    inactive_status = [{"active": False, "match_id": "1"}]

    with pytest.raises(tracker.PageRestartRequired, match="Scheduled restart"):
        monitor._wait_for_matches_with_hard_refresh(inactive_status)

    assert sleeps == [5]


@pytest.mark.parametrize(
    ("reload_result", "expected_status_source"),
    [(True, "collect"), (False, "collect"), (None, "hard")],
)
def test_do_periodic_reload_selects_refresh_path_and_synchronizes(
    monkeypatch, monitor, reload_result, expected_status_source
):
    statuses = [{"match_id": "1", "active": True}]
    calls = []
    reload_calls = []
    reload_intervals = iter((117,))
    monkeypatch.setattr(
        tracker, "get_table_live_reload_interval", lambda: next(reload_intervals)
    )
    monkeypatch.setattr(tracker.time, "monotonic", lambda: 50)
    monkeypatch.setattr(
        tracker,
        "reload_page_with_retries",
        lambda *args, **kwargs: reload_calls.append(kwargs) or reload_result,
    )
    monkeypatch.setattr(
        tracker,
        "collect_match_status",
        lambda *args, **kwargs: calls.append("collect") or statuses,
    )
    monkeypatch.setattr(
        monitor, "_do_hard_page_refresh_with_retries", lambda: calls.append("hard") or statuses
    )
    monkeypatch.setattr(
        monitor, "_synchronize_matches", lambda ids: calls.append(ids) or True
    )
    monkeypatch.setattr(tracker, "write_match_count", lambda *args: calls.append("write"))

    result = monitor._do_periodic_reload()

    assert result is True
    assert expected_status_source in calls
    assert ["1"] in calls
    assert calls[-1] == "write"
    assert monitor.next_reload_at == 167
    assert reload_calls == [{"shutdown_requested": monitor.shutdown_requested}]


def test_do_periodic_reload_waits_for_matches_when_status_is_inactive(monkeypatch, monitor):
    inactive = [{"match_id": None, "active": False}]
    active = [{"match_id": "1", "active": True}]
    calls = []
    monkeypatch.setattr(
        tracker,
        "reload_page_with_retries",
        lambda *args, **kwargs: False,
    )
    monkeypatch.setattr(
        tracker, "collect_match_status", lambda *args, **kwargs: inactive
    )
    monkeypatch.setattr(
        monitor, "_wait_for_matches_with_hard_refresh", lambda status: calls.append(status) or active
    )
    monkeypatch.setattr(
        monitor, "_synchronize_matches", lambda ids: calls.append(ids) or True
    )
    monkeypatch.setattr(tracker, "write_match_count", lambda *args: None)

    assert monitor._do_periodic_reload() is True
    assert calls == [inactive, ["1"]]


def test_wait_for_matches_collects_status_when_not_provided(monkeypatch, monitor):
    status = [{"match_id": "1", "active": True}]
    calls = []
    monkeypatch.setattr(
        tracker, "collect_match_status", lambda *args, **kwargs: calls.append(kwargs) or status
    )

    assert monitor._wait_for_matches_with_hard_refresh() == status
    assert calls == [{"require_crown": True}]


def test_wait_for_matches_refreshes_until_active_match(monkeypatch, monitor):
    empty = [{"match_id": None, "active": False}]
    active = [{"match_id": "1", "active": True}]
    refreshes = []
    sleeps = []
    monkeypatch.setattr(tracker.time, "sleep", sleeps.append)
    monkeypatch.setattr(
        monitor,
        "_do_hard_page_refresh_with_retries",
        lambda: refreshes.append(True) or active,
    )

    assert monitor._wait_for_matches_with_hard_refresh(empty) == active
    assert sleeps == [60]
    assert refreshes == [True]


def test_do_scheduled_page_refresh_syncs_and_polls(monkeypatch, monitor):
    status = [{"match_id": "1", "active": True}]
    calls = []
    monkeypatch.setattr(monitor, "_do_hard_page_refresh_with_retries", lambda: status)
    monkeypatch.setattr(monitor, "_wait_for_matches_with_hard_refresh", lambda value: value)
    monkeypatch.setattr(monitor, "_synchronize_matches", lambda ids: calls.append(ids) or True)
    monkeypatch.setattr(tracker, "write_match_count", lambda *args: calls.append("write"))
    monkeypatch.setattr(
        monitor, "_poll_and_update", lambda **kwargs: calls.append(kwargs) or False
    )
    monitor.active_match_ids = ["1"]

    assert monitor._do_scheduled_page_refresh() is True
    assert calls == [["1"], "write", {"log_data_loaded": True}]


def test_page_refresh_retries_then_returns_status(monkeypatch, monitor, caplog):
    attempts = []
    sleeps = []
    status = [{"match_id": "1", "active": True}]

    def refresh(page, wait_for_data):
        attempts.append(wait_for_data)
        if len(attempts) == 1:
            raise RuntimeError("temporary")

    monkeypatch.setattr(monitor, "page_refresh_callback", refresh)
    monkeypatch.setattr(tracker, "install_live_change_observer", lambda page: None)
    monkeypatch.setattr(
        tracker, "collect_match_status", lambda *args, **kwargs: status
    )
    monkeypatch.setattr(tracker.time, "sleep", sleeps.append)

    assert monitor._do_hard_page_refresh_with_retries(
        max_retries=2
    ) == status
    assert attempts == [False, False]
    assert sleeps == [10]
    assert "Page refresh attempt 1/2 failed" in caplog.text
    assert "Full page refresh" not in caplog.text


def test_page_refresh_stops_retrying_when_shutdown_is_requested(
    monkeypatch, monitor
):
    shutdown_requested = threading.Event()
    attempts = []
    sleeps = []
    monitor.shutdown_requested = shutdown_requested

    def refresh(page, wait_for_data):
        attempts.append(wait_for_data)
        shutdown_requested.set()
        raise RuntimeError("Target page, context or browser has been closed")

    monitor.page_refresh_callback = refresh
    monkeypatch.setattr(tracker.time, "sleep", sleeps.append)

    with pytest.raises(tracker.PageRestartRequired, match="Shutdown requested"):
        monitor._do_hard_page_refresh_with_retries(max_retries=3)

    assert attempts == [False]
    assert sleeps == []


def test_page_refresh_does_not_retry_when_page_is_closed(
    monkeypatch, monitor
):
    attempts = []
    saved = []

    def refresh(page, wait_for_data):
        attempts.append(wait_for_data)
        raise RuntimeError("Target page, context or browser has been closed")

    monitor.page_refresh_callback = refresh
    monkeypatch.setattr(
        monitor, "_save_state_to_json", lambda *args: saved.append(args) or True
    )

    with pytest.raises(tracker.PageRestartRequired, match="after 1 attempts"):
        monitor._do_hard_page_refresh_with_retries(max_retries=3)

    assert attempts == [False]
    assert saved == [(monitor.active_match_ids, monitor.last_data)]


def test_hard_page_refresh_missing_callback_saves_state_and_raises(
    monkeypatch, monitor
):
    monitor.page_refresh_callback = None
    saved = []
    monkeypatch.setattr(
        monitor, "_save_state_to_json", lambda *args: saved.append(args) or True
    )

    with pytest.raises(tracker.PageRestartRequired, match="callback is not configured"):
        monitor._do_hard_page_refresh_with_retries(max_retries=1)

    assert saved == [(monitor.active_match_ids, monitor.last_data)]


def test_hard_page_refresh_saves_failure_and_requests_restart_when_save_fails(
    monkeypatch, monitor, caplog
):
    monitor.page_refresh_callback = lambda *args, **kwargs: (_ for _ in ()).throw(
        RuntimeError("site unavailable")
    )
    monkeypatch.setattr(monitor, "_save_state_to_json", lambda *args: False)

    with pytest.raises(tracker.PageRestartRequired, match="failed after 1 attempts"):
        monitor._do_hard_page_refresh_with_retries(max_retries=1)

    assert "state save failed" in caplog.text
    assert "Page refresh failed after 1 attempts" in caplog.text
    assert "Full page refresh" not in caplog.text


def test_synchronize_matches_adds_deduplicated_new_ids_and_removes_old(
    monkeypatch, monitor
):
    old_data = market_data()
    new_data = market_data(score="2-0")
    monitor.active_match_ids = ["old", "stay"]
    monitor.match_history = {
        "old": {"initial": old_data, "changes": []},
        "stay": {"initial": old_data, "changes": []},
    }
    monitor.last_data = {"old": old_data, "stay": old_data}
    monkeypatch.setattr(
        tracker,
        "extract_all_match_data",
        lambda page, ids: {"new": new_data, "missing": None},
    )

    changed = monitor._synchronize_matches(["stay", "new", "new", "missing"])

    assert changed is True
    assert monitor.active_match_ids == ["stay", "new"]
    assert "old" not in monitor.match_history
    assert "old" not in monitor.last_data
    assert monitor.match_history["new"] == {"initial": new_data, "changes": []}
    assert monitor.last_data["new"]["score"] == "2-0"


def test_synchronize_matches_removes_pending_notifications_for_removed_matches(
    monkeypatch, monitor
):
    removed_data = market_data()
    active_data = market_data(score="1-0")
    monitor.active_match_ids = ["1", "stay"]
    monitor.match_history = {
        "1": {"initial": removed_data, "changes": []},
        "stay": {"initial": active_data, "changes": []},
    }
    monitor.last_data = {"1": removed_data, "stay": active_data}
    monitor.pending_notifications = {
        "10": {"match_id": 1},
        "11": {"match_id": "1"},
        "12": {"match_id": "stay"},
    }
    monkeypatch.setattr(tracker, "extract_all_match_data", lambda *args: {})

    assert monitor._synchronize_matches(["stay"]) is True

    assert monitor.active_match_ids == ["stay"]
    assert "1" not in monitor.match_history
    assert "1" not in monitor.last_data
    assert monitor.pending_notifications == {"12": {"match_id": "stay"}}


def test_synchronize_matches_returns_false_when_active_ids_unchanged(
    monkeypatch, monitor
):
    monitor.active_match_ids = ["1"]
    monkeypatch.setattr(
        tracker, "extract_all_match_data", lambda *args: pytest.fail("no new IDs")
    )

    assert monitor._synchronize_matches(["1", "1"]) is False
    assert monitor.active_match_ids == ["1"]


def test_poll_and_update_detects_market_time_and_score_changes(monkeypatch, monitor):
    old = market_data()
    changed = market_data(
        ah={"ah": "-0.5", "home_ah_odds": "1.8"},
        match_time="13",
        score="1-0",
    )
    monitor.active_match_ids = ["1", "2", "3"]
    monitor.last_data = {
        "1": {
            "ah": old["ah"],
            "ov": old["ov"],
            "match_time": old["match_time"],
            "score": old["score"],
        },
        "2": old,
    }
    monitor.match_history = {
        "1": {"initial": old, "changes": []},
        "2": {"initial": old, "changes": []},
    }
    monkeypatch.setattr(
        tracker,
        "extract_all_match_data",
        lambda page, ids: {"1": changed, "2": None, "3": changed},
    )

    result = monitor._poll_and_update(log_data_loaded=True)

    assert result is True
    assert monitor.match_history["1"]["changes"] == [changed]
    assert monitor.last_data["1"]["score"] == "1-0"
    assert monitor.match_history["2"]["changes"] == []


def test_poll_history_changes_remain_unique_after_json_restore(
    monkeypatch, monitor, tmp_path
):
    initial = market_data()
    current = market_data(score="1-0")
    monitor.active_match_ids = ["1"]
    monitor.last_data = {"1": tracker.state.get_last_data(initial)}
    monitor.match_history = {"1": {"initial": initial, "changes": []}}
    monkeypatch.setattr(
        tracker,
        "extract_all_match_data",
        lambda page, ids: {"1": current},
    )

    assert monitor._poll_and_update() is True
    current = market_data(score="1-1")
    assert monitor._poll_and_update() is True

    path = tmp_path / "match_state.json"
    assert monitor._save_state_to_json(["1"], monitor.last_data, path)
    saved = tracker.state.load_state_from_json(path)
    restored = tracker.state.restore_state(saved)

    entries = [
        restored["match_history"]["1"]["initial"],
        *restored["match_history"]["1"]["changes"],
    ]
    tracked_values = [
        json.dumps(
            [entry["ah"], entry["ov"], entry["match_time"], entry["score"]],
            sort_keys=True,
        )
        for entry in entries
    ]
    assert len(tracked_values) == len(set(tracked_values))


def test_poll_and_update_ignores_unchanged_data_and_missing_last_state(
    monkeypatch, monitor
):
    current = market_data()
    monitor.active_match_ids = ["1", "2"]
    monitor.last_data = {"1": current}
    monitor.match_history = {"1": {"initial": current, "changes": []}}
    monkeypatch.setattr(
        tracker, "extract_all_match_data", lambda *args: {"1": current, "2": current}
    )

    assert monitor._poll_and_update() is False
    assert monitor.match_history["1"]["changes"] == []


def test_parse_and_monitor_match_constructs_and_runs_monitor(monkeypatch):
    instances = []

    class FakeMonitor:
        def __init__(self, page, **kwargs):
            instances.append((page, kwargs))

        def run(self):
            instances.append("run")

    page = FakePage()
    callback = lambda current_page: None
    monkeypatch.setattr(tracker, "MatchMonitor", FakeMonitor)

    tracker.parse_and_monitor_match(
        page,
        ["1"],
        {"saved": True},
        live_refresh_callback=callback,
        page_refresh_callback=None,
        restart_deadline=123,
    )

    assert instances == [
        (
            page,
            {
                "match_ids": ["1"],
                "saved_state": {"saved": True},
                "live_refresh_callback": callback,
                "page_refresh_callback": None,
                "restart_deadline": 123,
                "shutdown_requested": None,
            },
        ),
        "run",
    ]


def test_monitor_loop_waits_once_then_propagates_wait_signal(monkeypatch, monitor):
    class StopLoop(Exception):
        pass

    observed = []
    monitor.next_reload_at = tracker.time.monotonic() + 10
    monitor.next_heartbeat_at = tracker.time.monotonic() + 20
    monitor.restart_deadline = tracker.time.monotonic() + 30
    monkeypatch.setattr(monitor, "_process_due_notifications", lambda: observed.append("process"))

    def wait(page, timeout_ms):
        observed.append((page, timeout_ms))
        raise StopLoop

    monkeypatch.setattr(tracker, "wait_for_live_change", wait)

    with pytest.raises(StopLoop):
        monitor._monitor_loop()

    assert observed[0] == "process"
    assert observed[1][0] is monitor.page
    assert observed[1][1] > 0


def test_module_exports_expected_public_api():
    assert tracker.__all__ == [
        "parse_and_monitor_match",
        "MatchMonitor",
        "PageRestartRequired",
    ]
