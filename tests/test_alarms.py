import pytest

from app import pending_notifications as alarms
from app.config import (
    NOTIFICATION_CHECK_DELAY_SECONDS,
    TELEGRAM_EDIT_RETRY_DELAY_SECONDS,
)


def market_data(*, over="2.5", handicap="-0.5"):
    return {
        "ah": {"ah": handicap},
        "ov": {"over": over},
    }


def process(
    monkeypatch,
    pending,
    last_data,
    active_match_ids=None,
    *,
    now=2,
    listed_leagues=("listed",),
    edit_result=True,
    mark_result=True,
):
    edits = []
    marks = []
    monkeypatch.setattr(alarms.time, "time", lambda: now)
    monkeypatch.setattr(alarms, "LEAGUES_LIST", list(listed_leagues))
    monkeypatch.setattr(
        alarms,
        "edit_telegram_notification",
        lambda *args: edits.append(args) or edit_result,
    )
    monkeypatch.setattr(
        alarms,
        "update_match_mark",
        lambda *args: marks.append(args) or mark_result,
    )
    alarms.process_due_notifications(
        pending,
        last_data,
        active_match_ids or [],
    )
    return edits, marks


def test_register_pending_notification_sets_deadline_and_string_key(monkeypatch):
    monkeypatch.setattr(alarms.time, "time", lambda: 100)
    pending = {}
    notification = {"message_id": 42, "match_id": "1", "market": "ov"}

    alarms.register_pending_notification(pending, notification)

    assert notification["deadline"] == 100 + NOTIFICATION_CHECK_DELAY_SECONDS
    assert pending == {"42": notification}


@pytest.mark.parametrize(
    ("market", "league", "market_data_value", "expected_marker"),
    [
        ("ov", "listed", {"ov": {"over": "Closed"}}, "🔥"),
        ("ov", "listed", {"ov": {"over": "2.5"}}, "⭐️"),
        ("ah", "unlisted", {"ah": {"ah": "Closed"}}, "🔒"),
        ("ah", "unlisted", {"ah": {"ah": "-0.5"}}, "💩"),
    ],
)
def test_process_due_notifications_edits_message_and_persists_mark(
    monkeypatch, market, league, market_data_value, expected_marker
):
    notification = {
        "message_id": "10",
        "match_id": "1",
        "market": market,
        "league": league,
        "message": "before 🔎 match",
        "deadline": 1,
        "link": "link",
        "prediction": "prediction",
        "channel_id": "channel",
    }
    pending = {"10": notification}
    last_data = {
        "1": {
            "ov": {"over": "2.5"},
            "ah": {"ah": "-0.5"},
        }
    }
    last_data["1"].update(market_data_value)

    edits, marks = process(
        monkeypatch,
        pending,
        last_data,
        ["1"],
    )

    assert notification["edited_message"] == f"before {expected_marker} match"
    assert notification["mark"] == expected_marker
    assert edits == [("10", notification["edited_message"], "channel")]
    assert marks == [("link", "prediction", expected_marker)]
    assert pending == {}


def test_process_due_notifications_ignores_items_before_deadline(monkeypatch):
    pending = {
        "10": {
            "deadline": 50,
            "message_id": "10",
            "match_id": "1",
        }
    }
    monkeypatch.setattr(alarms.time, "time", lambda: 49)
    monkeypatch.setattr(
        alarms,
        "edit_telegram_notification",
        lambda *args: pytest.fail("should not edit before deadline"),
    )

    alarms.process_due_notifications(pending, {}, [])

    assert "10" in pending


def test_process_due_notifications_retries_then_gives_up(monkeypatch, caplog):
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
    pending = {"10": item}
    last_data = {"1": market_data()}
    monkeypatch.setattr(alarms.time, "time", lambda: 100)
    monkeypatch.setattr(alarms, "edit_telegram_notification", lambda *args: False)

    alarms.process_due_notifications(pending, last_data, [])

    assert item["edit_attempts"] == 2
    assert item["deadline"] == 100 + TELEGRAM_EDIT_RETRY_DELAY_SECONDS
    assert pending["10"] is item
    assert "Retrying Telegram status edit" in caplog.text

    item["deadline"] = 1
    alarms.process_due_notifications(pending, last_data, [])

    assert "10" not in pending
    assert "1" not in last_data
    assert "Giving up Telegram status edit" in caplog.text


def test_process_due_notifications_keeps_data_while_another_alarm_uses_it(
    monkeypatch,
):
    pending = {
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
    last_data = {"1": market_data()}

    process(monkeypatch, pending, last_data)

    assert "10" not in pending
    assert "1" in last_data


def test_process_due_notifications_logs_mark_persistence_failure(
    monkeypatch, caplog
):
    pending = {
        "10": {
            "message_id": "10",
            "match_id": "1",
            "market": "ov",
            "league": "listed",
            "message": "🔎 Match",
            "deadline": 1,
            "link": "link",
            "prediction": "prediction",
        }
    }
    _, marks = process(
        monkeypatch,
        pending,
        {"1": market_data()},
        mark_result=False,
    )

    assert marks == [("link", "prediction", "⭐️")]
    assert "Failed to persist Telegram mark" in caplog.text


def test_process_due_notifications_removes_unused_match_data(monkeypatch):
    pending = {
        "10": {
            "message_id": "10",
            "match_id": "1",
            "market": "ov",
            "league": "listed",
            "message": "🔎 Match",
            "deadline": 1,
        }
    }
    last_data = {"1": market_data()}

    process(monkeypatch, pending, last_data)

    assert pending == {}
    assert last_data == {}


def test_process_due_notifications_retains_data_for_active_matches(monkeypatch):
    pending = {
        "10": {
            "message_id": "10",
            "match_id": "1",
            "market": "ov",
            "league": "listed",
            "message": "🔎 Match",
            "deadline": 1,
        }
    }
    last_data = {"1": market_data()}

    process(monkeypatch, pending, last_data, ["1"])

    assert "1" in last_data


def test_next_notification_timeout_returns_none_or_nearest_deadline(monkeypatch):
    monkeypatch.setattr(alarms.time, "time", lambda: 100)

    assert alarms.next_notification_timeout({}) is None
    assert alarms.next_notification_timeout(
        {"1": {"deadline": 101}, "2": {"deadline": 110}}
    ) == 1
    assert alarms.next_notification_timeout({"1": {"deadline": 99}}) == 0
