import json

import pytest

import tracker


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


def test_get_analysis_history_keeps_only_changes_to_tracked_fields():
    initial = market_data()
    ignored_change = {**market_data(), "date": "later"}
    score_change = market_data(score="1-0")
    market_change = market_data(over={"over": "Closed"})
    history = {
        "1": {
            "initial": initial,
            "changes": [ignored_change, score_change, market_change],
            "extra": True,
        }
    }

    result = tracker._get_analysis_history(history)

    assert result == {
        "1": {
            "initial": initial,
            "changes": [score_change, market_change],
            "extra": True,
        }
    }
    assert history["1"]["changes"] == [ignored_change, score_change, market_change]


def test_get_analysis_history_keeps_first_change_when_no_initial_entry():
    first = market_data()
    second = market_data(score="1-0")

    result = tracker._get_analysis_history(
        {"1": {"changes": [first, first.copy(), second]}}
    )

    assert result["1"]["changes"] == [first, second]


def test_load_state_from_json_returns_none_when_file_is_missing(tmp_path):
    assert tracker.load_state_from_json(tmp_path / "missing.json") is None


def test_load_state_from_json_loads_and_removes_file(tmp_path):
    path = tmp_path / "state.json"
    state = {"active_match_ids": ["1"]}
    path.write_text(json.dumps(state), encoding="utf-8")

    assert tracker.load_state_from_json(path) == state
    assert not path.exists()


def test_load_state_from_json_logs_invalid_json_and_leaves_file(
    tmp_path, caplog
):
    path = tmp_path / "state.json"
    path.write_text("{invalid", encoding="utf-8")

    assert tracker.load_state_from_json(path) is None
    assert path.exists()
    assert "Failed to load state" in caplog.text


def test_load_state_from_json_logs_remove_error(tmp_path, monkeypatch, caplog):
    path = tmp_path / "state.json"
    path.write_text('{"valid": true}', encoding="utf-8")
    monkeypatch.setattr(tracker.os, "remove", lambda path: (_ for _ in ()).throw(OSError("denied")))

    assert tracker.load_state_from_json(path) is None
    assert path.exists()
    assert "Failed to load state" in caplog.text


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
    assert result.consecutive_table_errors == 0


def test_run_installs_and_removes_observer_and_propagates_restart(monkeypatch, monitor):
    events = []
    restart = tracker.PageRestartRequired("restart")
    monkeypatch.setattr(monitor, "_init_or_restore_state", lambda: events.append("init"))
    monkeypatch.setattr(tracker, "_install_live_change_observer", lambda page: events.append("install"))
    monkeypatch.setattr(monitor, "_monitor_loop", lambda: (_ for _ in ()).throw(restart))
    monkeypatch.setattr(tracker, "_remove_live_change_observer", lambda page: events.append("remove"))

    with pytest.raises(tracker.PageRestartRequired, match="restart"):
        monitor.run()

    assert events == ["init", "install", "remove"]


def test_run_saves_state_and_wraps_unexpected_failure(monkeypatch, monitor, tmp_path):
    events = []
    monkeypatch.setattr(monitor, "_init_or_restore_state", lambda: setattr(monitor, "active_match_ids", ["1"]))
    monkeypatch.setattr(tracker, "_install_live_change_observer", lambda page: None)
    monkeypatch.setattr(monitor, "_monitor_loop", lambda: (_ for _ in ()).throw(ValueError("broken")))
    monkeypatch.setattr(tracker, "_remove_live_change_observer", lambda page: events.append("removed"))
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
    monkeypatch.setattr(tracker, "_install_live_change_observer", lambda page: None)
    monkeypatch.setattr(monitor, "_monitor_loop", lambda: (_ for _ in ()).throw(ValueError("broken")))
    monkeypatch.setattr(tracker, "_remove_live_change_observer", lambda page: None)
    monkeypatch.setattr(monitor, "_save_state_to_json", lambda *args: False)

    with pytest.raises(tracker.PageRestartRequired):
        monitor.run()

    assert "Could not save monitoring state before restart" in caplog.text


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


def test_init_or_restore_state_initializes_matches_and_runs_analyzer(
    monkeypatch, monitor
):
    initialized = market_data()
    calls = []
    monkeypatch.setattr(
        tracker,
        "_extract_all_match_data",
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


def test_run_analyzer_filters_history_and_calls_pattern_matcher(
    monkeypatch, monitor
):
    initial = market_data()
    unchanged = {**market_data(), "date": "later"}
    changed = market_data(score="1-0")
    monitor.match_history = {
        "1": {"initial": initial, "changes": [unchanged, changed]}
    }
    calls = []
    monkeypatch.setattr(
        tracker, "find_pattern_matches", lambda history, callback: calls.append((history, callback))
    )

    monitor._run_analyzer()

    assert len(calls) == 1
    assert calls[0][0]["1"]["changes"] == [changed]
    assert calls[0][1].__self__ is monitor
    assert calls[0][1].__func__ is tracker.MatchMonitor._register_pending_notification


def test_run_analyzer_writes_debug_snapshot(monkeypatch, monitor, tmp_path):
    monkeypatch.setattr(tracker, "DEBUGMODE", 1)
    monkeypatch.setattr(tracker, "__file__", str(tmp_path / "tracker.py"))
    monkeypatch.setattr(tracker, "find_pattern_matches", lambda *args: None)
    monitor.match_history = {"1": {"initial": market_data(), "changes": []}}

    monitor._run_analyzer()

    snapshot = json.loads((tmp_path / "data_closed_ov_ah.json").read_text(encoding="utf-8"))
    assert snapshot["1"]["initial"] == monitor.match_history["1"]["initial"]


def test_register_pending_notification_sets_deadline_and_keys_by_string_id(
    monkeypatch, monitor
):
    monkeypatch.setattr(tracker.time, "time", lambda: 100)
    notification = {"message_id": 42, "match_id": "1", "market": "ov"}

    monitor._register_pending_notification(notification)

    assert notification["deadline"] == 100 + tracker.NOTIFICATION_CHECK_DELAY_SECONDS
    assert monitor.pending_notifications == {"42": notification}


@pytest.mark.parametrize(
    ("market_data_value", "league", "expected_marker", "prefix"),
    [
        ({"ov": {"over": "Closed"}}, "listed", "🔥", "🔎 "),
        ({"ov": {"over": "2.5"}}, "listed", "⭐️", "🔎 "),
        ({"ah": {"ah": "Closed"}}, "unlisted", "🔒", "⚽️ "),
        ({"ah": {"ah": "-0.5"}}, "unlisted", "💩", "⚽️ "),
    ],
)
def test_process_due_notifications_selects_marker_and_persists_success(
    monkeypatch, monitor, market_data_value, league, expected_marker, prefix
):
    notification = {
        "message_id": "10",
        "match_id": "1",
        "market": "ov" if "ov" in market_data_value else "ah",
        "league": league,
        "message": f"before {prefix}match",
        "deadline": 1,
        "link": "link",
        "prediction": "prediction",
        "channel_id": "channel",
    }
    monitor.pending_notifications["10"] = notification
    monitor.last_data["1"] = market_data_value
    monitor.active_match_ids = ["1"]
    monkeypatch.setattr(tracker.time, "time", lambda: 2)
    monkeypatch.setattr(tracker, "LEAGUES_LIST", ["listed"])
    edits = []
    marks = []
    monkeypatch.setattr(
        tracker,
        "edit_telegram_notification",
        lambda *args: edits.append(args) or True,
    )
    monkeypatch.setattr(
        tracker, "update_match_mark", lambda *args: marks.append(args) or True
    )

    monitor._process_due_notifications()

    assert notification["edited_message"] == f"before {expected_marker} match"
    assert notification["mark"] == expected_marker
    assert edits == [("10", notification["edited_message"], "channel")]
    assert marks == [("link", "prediction", expected_marker, "channel")]
    assert monitor.pending_notifications == {}


def test_process_due_notifications_ignores_not_yet_due_item(monkeypatch, monitor):
    monitor.pending_notifications["10"] = {
        "deadline": 50,
        "message_id": "10",
        "match_id": "1",
    }
    monkeypatch.setattr(tracker.time, "time", lambda: 49)
    monkeypatch.setattr(
        tracker,
        "edit_telegram_notification",
        lambda *args: pytest.fail("should not edit before deadline"),
    )

    monitor._process_due_notifications()

    assert "10" in monitor.pending_notifications


def test_process_due_notifications_retries_failed_edit_then_gives_up(
    monkeypatch, monitor, caplog
):
    item = {
        "message_id": "10",
        "match_id": "1",
        "market": "ov",
        "league": "listed",
        "message": "🔎 Match",
        "deadline": 1,
        "edited_message": "🔥 Match",
        "mark": "🔥",
        "edit_attempts": 1,
    }
    monitor.pending_notifications["10"] = item
    monkeypatch.setattr(tracker.time, "time", lambda: 100)
    monkeypatch.setattr(tracker, "edit_telegram_notification", lambda *args: False)

    monitor._process_due_notifications()

    assert item["edit_attempts"] == 2
    assert item["deadline"] == 130
    assert monitor.pending_notifications["10"] is item
    assert "Retrying Telegram status edit" in caplog.text

    item["deadline"] = 1
    monitor._process_due_notifications()

    assert "10" not in monitor.pending_notifications
    assert "Giving up Telegram status edit" in caplog.text


def test_process_due_notifications_keeps_shared_match_data_until_other_pending_done(
    monkeypatch, monitor
):
    monitor.active_match_ids = []
    monitor.last_data["1"] = market_data()
    monitor.pending_notifications = {
        "10": {
            "message_id": "10",
            "match_id": "1",
            "market": "ov",
            "league": "listed",
            "message": "🔎 Match",
            "deadline": 1,
        },
        "11": {"message_id": "11", "match_id": "1", "deadline": 100},
    }
    monkeypatch.setattr(tracker.time, "time", lambda: 2)
    monkeypatch.setattr(tracker, "LEAGUES_LIST", ["listed"])
    monkeypatch.setattr(tracker, "edit_telegram_notification", lambda *args: True)
    monkeypatch.setattr(tracker, "update_match_mark", lambda *args: True)

    monitor._process_due_notifications()

    assert "10" not in monitor.pending_notifications
    assert "1" in monitor.last_data


def test_process_due_notifications_logs_mark_persistence_failure(
    monkeypatch, monitor, caplog
):
    monitor.pending_notifications["10"] = {
        "message_id": "10",
        "match_id": "1",
        "market": "ov",
        "league": "listed",
        "message": "🔎 Match",
        "deadline": 1,
        "link": "link",
        "prediction": "prediction",
    }
    monkeypatch.setattr(tracker.time, "time", lambda: 2)
    monkeypatch.setattr(tracker, "LEAGUES_LIST", ["listed"])
    monkeypatch.setattr(tracker, "edit_telegram_notification", lambda *args: True)
    monkeypatch.setattr(tracker, "update_match_mark", lambda *args: False)

    monitor._process_due_notifications()

    assert "Failed to persist Telegram mark" in caplog.text


def test_next_notification_timeout_returns_none_or_time_until_earliest(
    monkeypatch, monitor
):
    monkeypatch.setattr(tracker.time, "time", lambda: 100)
    assert monitor._next_notification_timeout() is None
    monitor.pending_notifications = {
        "1": {"deadline": 101},
        "2": {"deadline": 110},
    }

    assert monitor._next_notification_timeout() == 1

    monitor.pending_notifications["1"]["deadline"] = 99
    assert monitor._next_notification_timeout() == 0


def test_load_initial_data_sets_history_and_last_data(monkeypatch, monitor):
    initial = market_data()
    monitor.active_match_ids = ["1", "2"]
    monitor.consecutive_table_errors = 3
    monkeypatch.setattr(
        tracker, "_extract_all_match_data", lambda page, ids: {"1": initial, "2": None}
    )

    monitor._load_initial_data()

    assert monitor.consecutive_table_errors == 0
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

    with pytest.raises(tracker.PageRestartRequired, match="Scheduled restart"):
        monitor._trigger_scheduled_restart()

    assert saved == [(monitor.active_match_ids, monitor.last_data)]


@pytest.mark.parametrize(
    ("reload_result", "expected_status_source"),
    [(True, "collect"), (False, "collect"), (None, "hard")],
)
def test_do_periodic_reload_selects_refresh_path_and_synchronizes(
    monkeypatch, monitor, reload_result, expected_status_source
):
    statuses = [{"match_id": "1", "active": True}]
    calls = []
    monkeypatch.setattr(tracker, "_reload_page_with_retries", lambda *args: reload_result)
    monkeypatch.setattr(
        tracker,
        "_collect_match_status",
        lambda *args, **kwargs: calls.append("collect") or statuses,
    )
    monkeypatch.setattr(
        monitor, "_do_hard_page_refresh_with_retries", lambda: calls.append("hard") or statuses
    )
    monkeypatch.setattr(
        monitor, "_synchronize_matches", lambda ids: calls.append(ids) or True
    )
    monkeypatch.setattr(tracker, "_write_match_count", lambda *args: calls.append("write"))

    result = monitor._do_periodic_reload()

    assert result is True
    assert expected_status_source in calls
    assert ["1"] in calls
    assert calls[-1] == "write"


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
    monkeypatch.setattr(tracker, "_write_match_count", lambda *args: calls.append("write"))
    monkeypatch.setattr(
        monitor, "_poll_and_update", lambda **kwargs: calls.append(kwargs) or (False, False)
    )
    monitor.active_match_ids = ["1"]

    assert monitor._do_scheduled_page_refresh() is True
    assert calls == [["1"], "write", {"log_data_loaded": True}]


def test_hard_page_refresh_retries_then_returns_status(monkeypatch, monitor):
    attempts = []
    sleeps = []
    status = [{"match_id": "1", "active": True}]

    def refresh(page, wait_for_data):
        attempts.append(wait_for_data)
        if len(attempts) == 1:
            raise RuntimeError("temporary")

    monkeypatch.setattr(monitor, "page_refresh_callback", refresh)
    monkeypatch.setattr(tracker, "_install_live_change_observer", lambda page: None)
    monkeypatch.setattr(
        tracker, "_collect_match_status", lambda *args, **kwargs: status
    )
    monkeypatch.setattr(tracker.time, "sleep", sleeps.append)

    assert monitor._do_hard_page_refresh_with_retries(
        max_retries=2, retry_delay=3
    ) == status
    assert attempts == [False, False]
    assert sleeps == [3]


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
    monitor.consecutive_table_errors = 5
    monkeypatch.setattr(
        tracker,
        "_extract_all_match_data",
        lambda page, ids: {"new": new_data, "missing": None},
    )

    changed = monitor._synchronize_matches(["stay", "new", "new", "missing"])

    assert changed is True
    assert monitor.active_match_ids == ["stay", "new"]
    assert "old" not in monitor.match_history
    assert "old" not in monitor.last_data
    assert monitor.match_history["new"] == {"initial": new_data, "changes": []}
    assert monitor.last_data["new"]["score"] == "2-0"
    assert monitor.consecutive_table_errors == 0


def test_synchronize_matches_retains_removed_match_data_for_pending_notification(
    monkeypatch, monitor
):
    old_data = market_data()
    monitor.active_match_ids = ["1"]
    monitor.match_history = {"1": {"initial": old_data, "changes": []}}
    monitor.last_data = {"1": old_data}
    monitor.pending_notifications = {"10": {"match_id": 1}}
    monkeypatch.setattr(tracker, "_extract_all_match_data", lambda *args: {})

    assert monitor._synchronize_matches([]) is True
    assert monitor.active_match_ids == []
    assert "1" not in monitor.match_history
    assert "1" in monitor.last_data


def test_synchronize_matches_returns_false_when_active_ids_unchanged(
    monkeypatch, monitor
):
    monitor.active_match_ids = ["1"]
    monkeypatch.setattr(
        tracker, "_extract_all_match_data", lambda *args: pytest.fail("no new IDs")
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
        "_extract_all_match_data",
        lambda page, ids: {"1": changed, "2": None, "3": changed},
    )

    result = monitor._poll_and_update(log_data_loaded=True)

    assert result == (True, False)
    assert monitor.match_history["1"]["changes"] == [changed]
    assert monitor.last_data["1"]["score"] == "1-0"
    assert monitor.match_history["2"]["changes"] == []
    assert monitor.consecutive_table_errors == 0


def test_poll_and_update_ignores_unchanged_data_and_missing_last_state(
    monkeypatch, monitor
):
    current = market_data()
    monitor.active_match_ids = ["1", "2"]
    monitor.last_data = {"1": current}
    monitor.match_history = {"1": {"initial": current, "changes": []}}
    monkeypatch.setattr(
        tracker, "_extract_all_match_data", lambda *args: {"1": current, "2": current}
    )

    assert monitor._poll_and_update() == (False, False)
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
    )

    assert instances == [
        (
            page,
            {
                "match_ids": ["1"],
                "saved_state": {"saved": True},
                "live_refresh_callback": callback,
                "page_refresh_callback": None,
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
    monitor.restart_deadline = tracker.time.time() + 30
    monkeypatch.setattr(monitor, "_process_due_notifications", lambda: observed.append("process"))

    def wait(page, timeout_ms):
        observed.append((page, timeout_ms))
        raise StopLoop

    monkeypatch.setattr(tracker, "_wait_for_live_change", wait)

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
