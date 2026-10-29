import threading

import pytest

from app import notifier
from app.config import CHANNEL_ID, SITE_URL


@pytest.mark.parametrize(
    ("raw_time", "expected"),
    [
        (None, "Unknown"),
        ("", "Unknown"),
        ("  ", "Unknown"),
        ("Unknown", "Unknown"),
        (" ht ", "45"),
        ("fT", "FT"),
        ("eT", "ET"),
        ("et+2", "et+2"),
        ("  37  ", "37"),
    ],
)
def test_normalize_match_time_for_message(raw_time, expected):
    assert notifier._normalize_match_time_for_message(raw_time) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, None),
        ("2.5", "2.5"),
        ("0.5/1", "0.75"),
        ("-0.5/-1", "-0.75"),
        ("0.5/-1", "-0.75"),
        ("1/1.5", "1.25"),
        ("1/2/3", "1/2/3"),
        ("1/x", "1/x"),
        (" / ", " / "),
        (1.5, 1.5),
    ],
)
def test_normalize_split_value(value, expected):
    assert notifier._normalize_split_value(value) == expected


@pytest.mark.parametrize(
    ("over", "handicap", "team_order", "expected"),
    [
        ("2.5/3", None, None, "Over 2.75"),
        (None, "0.5/1", "Home", "Handicap 0.75 Home"),
        (None, "-0.5/-1", "Away", "Handicap -0.75 Away"),
        (None, "", None, "Handicap  None"),
        (None, None, "Home", "Over None"),
    ],
)
def test_build_prediction(over, handicap, team_order, expected):
    assert notifier._build_prediction(over, handicap, team_order) == expected


@pytest.mark.parametrize(
    ("over", "handicap", "team_order", "score", "expected"),
    [
        ("3.25", None, None, "0-0", "Тотал больше 3.5"),
        ("3.75", None, None, "0-0", "Тотал больше 4"),
        ("2.5", None, None, "0-0", "Тотал больше 2.5"),
        ("2.5/3", None, None, "0-0", "Тотал больше 3"),
        (None, "-1.5", "Home", "0-1", "Победа 1"),
        (None, "-2", "Home", "0-2", "Фора 1 0"),
        (None, "-0.25", "Home", "1-2", "Фора 1 +1"),
        (None, "-3.5", "Home", "1-0", "Фора 1 -4.5"),
        (None, "-1.5", "Away", "1-0", "Победа 2"),
        (None, "+0.5", "Home", "0-0", "1X"),
        (None, "+0.25", "Home", "0-0", "1X"),
        (None, "+0.5", "Away", "0-0", "X2"),
        (None, "-0.25", "Away", "0-0", "Победа 2"),
        (None, "-0.75", "Home", "0-0", "Фора 1 -1"),
        (None, "-1.25", "Away", "0-0", "Фора 2 -1.5"),
        (None, "+0.75", "Home", "0-0", "Фора 1 +1"),
        (None, "0", "Home", "0-0", "Фора 1 0"),
        (None, "1.5", "Away", "0-0", "Фора 2 +1.5"),
        (None, "+1.25", "Home", "0-0", "Фора 1 +1.5"),
    ],
)
def test_build_display_prediction(
    over, handicap, team_order, score, expected
):
    assert notifier._build_display_prediction(
        over, handicap, team_order, score
    ) == expected


def test_build_display_prediction_leaves_handicap_unadjusted_without_score(
    caplog,
):
    assert notifier._build_display_prediction(
        None, "-1.25", "Home", "Unknown"
    ) == "Фора 1 -1.25"
    assert notifier._build_display_prediction(
        None, "-0.5", "Home", "Unknown"
    ) == "Фора 1 -0.5"
    assert "Could not recalculate handicap" in caplog.text


@pytest.mark.parametrize(
    ("over", "handicap", "team_order", "score", "expected", "warning"),
    [
        ("invalid", None, None, "0-0", "Тотал больше invalid", "format total"),
        (None, "-0.5", "Unknown", "0-0", "Фора -0.5 Unknown", "map handicap"),
        (None, "invalid", "Home", "0-0", "Фора 1 invalid", "parse handicap"),
        (None, "-0.5", "Home", None, "Фора 1 -0.5", "unknown score"),
    ],
)
def test_build_display_prediction_falls_back_for_invalid_inputs(
    caplog, over, handicap, team_order, score, expected, warning
):
    assert notifier._build_display_prediction(
        over, handicap, team_order, score
    ) == expected
    assert warning in caplog.text


def test_build_message_linkifies_match_and_normalizes_time():
    message = notifier._build_message(
        "League",
        "Home",
        "Away",
        "1-0",
        "https://example.test/match/1",
        "Over 2.5",
        "HT",
    )

    assert message == (
        "<b>League</b>\n"
        "🔎 45’ <a href=\"https://example.test/match/1\">Home 1-0 Away</a>\n\n"
        "Over 2.5"
    )


def test_build_message_without_match_url_leaves_match_text_unlinked():
    message = notifier._build_message(
        "League", "Home", "Away", "0-0", "", "Over 2.5"
    )

    assert "Home 0-0 Away" in message
    assert "<a " not in message
    assert "🔎 Unknown’" in message


class FakeResponse:
    def __init__(self, status_code=200, json_data=None, text="response error", json_error=None):
        self.status_code = status_code
        self._json_data = json_data
        self.text = text
        self._json_error = json_error

    def json(self):
        if self._json_error:
            raise self._json_error
        return self._json_data


def test_send_message_posts_expected_request_and_returns_json(monkeypatch):
    calls = []
    response_data = {"ok": True, "result": {"message_id": 10}}

    def fake_post(*args, **kwargs):
        calls.append((args, kwargs))
        return FakeResponse(json_data=response_data)

    monkeypatch.setattr(notifier.requests, "post", fake_post)

    result = notifier._send_message(
        {"text": "hello"}, match_id="match-1", success_message="sent"
    )

    assert result == response_data
    assert calls == [
        (
            (notifier.TELEGRAM_API_URL,),
            {
                "json": {"text": "hello"},
                "proxies": notifier.TELEGRAM_PROXIES,
                "timeout": 10,
            },
        )
    ]


@pytest.mark.parametrize(
    ("responses", "exception", "expected_calls", "expected_sleeps"),
    [
        ([FakeResponse(status_code=500)], None, 1, []),
        ([FakeResponse(json_error=ValueError("bad json"))], None, 1, []),
        ([FakeResponse(json_data={"ok": False, "description": "denied"})], None, 1, []),
        ([], RuntimeError("network down"), 1, []),
    ],
)
def test_send_message_returns_none_after_single_failure(
    monkeypatch, responses, exception, expected_calls, expected_sleeps
):
    calls = []
    sleeps = []

    def fake_post(*args, **kwargs):
        calls.append((args, kwargs))
        if exception:
            raise exception
        return responses.pop(0)

    monkeypatch.setattr(notifier.requests, "post", fake_post)
    monkeypatch.setattr(notifier.time, "sleep", sleeps.append)

    assert notifier._send_message({"text": "hello"}) is None
    assert len(calls) == expected_calls
    assert sleeps == expected_sleeps


def test_send_message_retries_failures_and_returns_next_success(monkeypatch):
    responses = [
        FakeResponse(status_code=503),
        FakeResponse(json_data={"ok": True}),
    ]
    sleeps = []
    monkeypatch.setattr(notifier.requests, "post", lambda *args, **kwargs: responses.pop(0))
    monkeypatch.setattr(notifier.time, "sleep", sleeps.append)

    result = notifier._send_message(
        {}, max_attempts=3, retry_delay=0.25, api_url="https://example.test"
    )

    assert result == {"ok": True}
    assert sleeps == [0.25]


def test_send_message_stops_retrying_when_shutdown_interrupts_retry_wait(
    monkeypatch,
):
    shutdown_requested = threading.Event()
    calls = []

    def fail_request(*args, **kwargs):
        calls.append((args, kwargs))
        return FakeResponse(status_code=503)

    def interrupt_wait(seconds):
        shutdown_requested.set()
        return True

    monkeypatch.setattr(notifier.requests, "post", fail_request)
    monkeypatch.setattr(shutdown_requested, "wait", interrupt_wait)

    assert notifier._send_message(
        {},
        max_attempts=3,
        retry_delay=5,
        shutdown_requested=shutdown_requested,
    ) is None
    assert len(calls) == 1


def test_send_message_does_not_start_if_shutdown_is_already_requested(
    monkeypatch,
):
    shutdown_requested = threading.Event()
    shutdown_requested.set()
    calls = []
    monkeypatch.setattr(
        notifier.requests, "post", lambda *args, **kwargs: calls.append(args)
    )

    assert notifier._send_message(
        {}, max_attempts=3, shutdown_requested=shutdown_requested
    ) is None
    assert calls == []


def test_send_message_retries_rejected_telegram_response(monkeypatch):
    responses = [
        FakeResponse(json_data={"ok": False, "description": "retry"}),
        FakeResponse(json_data={"ok": True, "result": {}}),
    ]
    sleeps = []
    monkeypatch.setattr(notifier.requests, "post", lambda *args, **kwargs: responses.pop(0))
    monkeypatch.setattr(notifier.time, "sleep", sleeps.append)

    assert notifier._send_message({}, max_attempts=2) == {
        "ok": True,
        "result": {},
    }
    assert sleeps == [5]


def test_send_message_with_zero_attempts_does_not_post(monkeypatch):
    calls = []
    monkeypatch.setattr(notifier.requests, "post", lambda *args, **kwargs: calls.append(args))

    assert notifier._send_message({}, max_attempts=0) is None
    assert calls == []


def test_edit_telegram_notification_uses_edit_endpoint_and_fallback_channel(monkeypatch):
    calls = []
    monkeypatch.setattr(
        notifier,
        "_send_message",
        lambda *args, **kwargs: calls.append((args, kwargs)) or {"ok": True},
    )

    assert notifier.edit_telegram_notification(44, "updated", channel_id="") is True

    assert calls == [
        (
            (
                {
                    "chat_id": CHANNEL_ID,
                    "message_id": 44,
                    "text": "updated",
                    "parse_mode": "HTML",
                    "disable_web_page_preview": True,
                },
            ),
            {
                "success_message": "Telegram notification 44 updated",
                "api_url": notifier.TELEGRAM_EDIT_MESSAGE_URL,
                "shutdown_requested": None,
            },
        )
    ]


def test_edit_telegram_notification_uses_explicit_channel_and_failure(monkeypatch):
    calls = []
    monkeypatch.setattr(
        notifier,
        "_send_message",
        lambda *args, **kwargs: calls.append(args[0]) or None,
    )

    assert notifier.edit_telegram_notification(
        44, "updated", channel_id="custom-channel"
    ) is False
    assert calls[0]["chat_id"] == "custom-channel"


def test_edit_telegram_notification_forwards_shutdown_event(monkeypatch):
    shutdown_requested = threading.Event()
    calls = []
    monkeypatch.setattr(
        notifier,
        "_send_message",
        lambda *args, **kwargs: calls.append((args, kwargs)) or {"ok": True},
    )

    assert notifier.edit_telegram_notification(
        44, "updated", shutdown_requested=shutdown_requested
    )
    assert calls[0][1]["shutdown_requested"] is shutdown_requested


def test_save_notification_forwards_fields_to_storage(monkeypatch):
    calls = []
    monkeypatch.setattr(
        notifier, "save_match", lambda **kwargs: calls.append(kwargs)
    )

    notifier._save_notification(
        "League",
        "Home",
        "Away",
        "Over 2.5",
        1.6,
        "ODDS 1.9 -> 1.6",
        "match-url",
        "match-id",
        "ov",
        "old",
    )

    assert calls == [
        {
            "league": "League",
            "home_team": "Home",
            "away_team": "Away",
            "prediction": "Over 2.5",
            "odds": 1.6,
            "drop_type": "ODDS 1.9 -> 1.6",
            "link": "match-url",
            "match_id": "match-id",
            "market": "ov",
            "strategy": "old",
        }
    ]


def test_save_notification_logs_storage_failure(monkeypatch, caplog):
    def fail_save(**kwargs):
        raise OSError("storage unavailable")

    monkeypatch.setattr(notifier, "save_match", fail_save)

    notifier._save_notification(
        "League", "Home", "Away", "Over 2.5", 1.6, None, "url",
        "id", "ov", "old"
    )

    assert "Failed to save match: storage unavailable" in caplog.text


@pytest.fixture
def notification_dependencies(monkeypatch):
    calls = {"sent": [], "saved": [], "market_duplicate": []}
    monkeypatch.setattr(
        notifier,
        "check_duplicate_market",
        lambda *args: calls["market_duplicate"].append(args) or False,
    )
    monkeypatch.setattr(
        notifier,
        "_send_message",
        lambda *args, **kwargs: calls["sent"].append((args, kwargs))
        or {"ok": True, "result": {"message_id": 123}},
    )
    monkeypatch.setattr(
        notifier, "_save_notification", lambda *args: calls["saved"].append(args)
    )
    return calls


def send_over(**overrides):
    arguments = {
        "league": "League",
        "team1": "Home",
        "team2": "Away",
        "score": "1-0",
        "over": "2.5",
        "over_odds": 1.55,
        "match_id": "match-1",
        "match_time": "HT",
        "drop_type": "ODDS 1.9 -> 1.55",
    }
    arguments.update(overrides)
    return notifier.send_telegram_notification(**arguments)


def test_send_telegram_notification_sends_saves_and_calls_callbacks(
    notification_dependencies,
):
    calls = notification_dependencies
    details_callback = []

    result = send_over(
        on_sent_details=lambda *args: details_callback.append(args),
        channel_id="custom-channel",
        strategy="new",
    )

    message = (
        "<b>League</b>\n"
        "🔎 45’ <a href=\""
        f"{SITE_URL}oddscomp/match-1"
        "\">Home 1-0 Away</a>\n\n"
        "Тотал больше 2.5"
    )
    match_url = f"{SITE_URL}oddscomp/match-1"
    assert result is True
    assert calls["market_duplicate"] == [("match-1", "ov")]
    assert calls["sent"] == [
        (
            (
                {
                    "chat_id": "custom-channel",
                    "text": message,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": True,
                },
                "match-1",
            ),
            {
                "max_attempts": 3,
                "retry_delay": 5,
                "shutdown_requested": None,
            },
        )
    ]
    assert calls["saved"] == [
        (
            "League",
            "Home",
            "Away",
            "Over 2.5",
            1.55,
            "ODDS 1.9 -> 1.55",
            match_url,
            "match-1",
            "ov",
            "new",
        )
    ]
    assert details_callback == [
        (123, message, match_url, "Over 2.5", "custom-channel")
    ]


def test_send_telegram_notification_forwards_shutdown_event(
    notification_dependencies,
):
    shutdown_requested = threading.Event()

    assert send_over(shutdown_requested=shutdown_requested) is True
    assert notification_dependencies["sent"][0][1]["shutdown_requested"] is shutdown_requested


def test_send_telegram_notification_builds_handicap_signal(notification_dependencies):
    calls = notification_dependencies

    assert send_over(
        score="0-0",
        handicap_text="-0.5/ -1",
        handicap_team_order="Away",
        channel_id=None,
        strategy=None,
    )

    args = calls["sent"][0][0]
    assert args[0]["chat_id"] == CHANNEL_ID
    assert "Фора 2 -1" in args[0]["text"]
    assert " · " not in args[0]["text"]
    assert calls["market_duplicate"] == [("match-1", "ah")]
    assert calls["saved"][0][3] == "Handicap -0.75 Away"
    assert calls["saved"][0][8:] == ("ah", "default")


def test_send_telegram_notification_converts_display_but_keeps_internal_prediction(
    notification_dependencies,
):
    calls = notification_dependencies
    details_callback = []

    assert send_over(
        score="0 - 1",
        handicap_text="-1.5",
        handicap_team_order="Home",
        on_sent_details=lambda *args: details_callback.append(args),
    )

    assert "Победа 1" in calls["sent"][0][0][0]["text"]
    assert " · " not in calls["sent"][0][0][0]["text"]
    assert calls["saved"][0][3] == "Handicap -1.5 Home"
    assert details_callback[0][3] == "Handicap -1.5 Home"


def test_send_telegram_notification_without_match_id_has_no_match_link(
    notification_dependencies,
):
    calls = notification_dependencies

    assert send_over(match_id=None)

    assert calls["sent"][0][0][0]["text"].find("<a ") == -1
    assert calls["saved"][0][6] == ""
    assert calls["market_duplicate"] == [(None, "ov")]


def test_send_telegram_notification_returns_false_when_market_is_duplicate(
    monkeypatch,
):
    calls = {"sent": [], "saved": []}
    monkeypatch.setattr(
        notifier,
        "check_duplicate_market",
        lambda *args: True,
    )
    monkeypatch.setattr(
        notifier, "_send_message", lambda *args, **kwargs: calls["sent"].append(args)
    )
    monkeypatch.setattr(
        notifier, "_save_notification", lambda *args: calls["saved"].append(args)
    )

    assert send_over() is False
    assert calls == {"sent": [], "saved": []}


def test_send_telegram_notification_returns_false_when_send_fails(
    notification_dependencies, monkeypatch
):
    calls = notification_dependencies
    monkeypatch.setattr(notifier, "_send_message", lambda *args, **kwargs: None)

    assert send_over() is False
    assert calls["saved"] == []


@pytest.mark.parametrize(
    ("response", "expects_error"),
    [
        ({"ok": True, "result": {}}, True),
        ({"ok": True, "result": None}, True),
        ({"ok": True}, True),
        ({"ok": True, "result": {"message_id": 123}}, False),
    ],
)
def test_send_telegram_notification_handles_missing_message_id(
    notification_dependencies, monkeypatch, response, expects_error, caplog
):
    calls = notification_dependencies
    monkeypatch.setattr(notifier, "_send_message", lambda *args, **kwargs: response)

    assert send_over(on_sent_details=lambda *args: None) is True
    assert len(calls["saved"]) == 1
    assert ("did not include a message_id" in caplog.text) is expects_error


def test_send_telegram_notification_without_tracking_callback_needs_no_message_id(
    notification_dependencies, monkeypatch, caplog
):
    monkeypatch.setattr(
        notifier,
        "_send_message",
        lambda *args, **kwargs: {"ok": True, "result": {}},
    )

    assert send_over() is True
    assert "did not include a message_id" not in caplog.text


def test_send_telegram_notification_logs_callback_errors_without_failing(
    notification_dependencies, caplog
):
    def fail_callback(*args):
        raise RuntimeError("callback failed")

    assert send_over(on_sent_details=fail_callback) is True
    assert "Failed to register Telegram notification" in caplog.text
