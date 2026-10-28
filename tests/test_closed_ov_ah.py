import threading

import pytest

from app import closed_ov_ah
from app.config import START_ODD, THRESHOLD


def entry(
    *,
    over=None,
    over_odds=None,
    handicap=None,
    home_odds=None,
    away_odds=None,
    **metadata,
):
    return {
        "ov": {"over": over, "over_odds": over_odds},
        "ah": {
            "ah": handicap,
            "home_ah_odds": home_odds,
            "away_ah_odds": away_odds,
        },
        **metadata,
    }


def closed_entry(market, **metadata):
    return {
        market: {market if market == "ah" else "over": "Closed"},
        **metadata,
    }


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("1.25", 1.25),
        (2, 2.0),
        (None, None),
        ("not-a-number", None),
    ],
)
def test_to_float(value, expected):
    assert closed_ov_ah._to_float(value) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, None),
        ("", None),
        ("  ", None),
        ("  -0.5  ", "-0.5"),
        (0.5, "0.5"),
    ],
)
def test_normalize_ah_text(value, expected):
    assert closed_ov_ah._normalize_ah_text(value) == expected


@pytest.mark.parametrize("value", ["0", "0.0", "+0", "-0", "-0.0"])
def test_is_exact_zero_handicap_accepts_supported_zero_forms(value):
    assert closed_ov_ah._is_exact_zero_ah(value)


@pytest.mark.parametrize("value", [None, "", "0.5", "0/-0.5", "0.00"])
def test_is_exact_zero_handicap_rejects_non_matching_values(value):
    assert not closed_ov_ah._is_exact_zero_ah(value)


@pytest.mark.parametrize(
    "value",
    ["0/-0.5", "+0/-1", "0 / -0,5"],
)
def test_is_away_zero_split_handicap_accepts_supported_forms(value):
    assert closed_ov_ah._is_away_zero_split_handicap(value)


@pytest.mark.parametrize("value", [None, "", "0/0.5", "-0/-0.5", "0 / -"])
def test_is_away_zero_split_handicap_rejects_invalid_forms(value):
    assert not closed_ov_ah._is_away_zero_split_handicap(value)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, None),
        ("", None),
        ("0", 0),
        ("-0.0", 0),
        ("0.5", 1),
        ("-0.5", -1),
        ("0/-0.5", -1),
    ],
)
def test_ah_sign(value, expected):
    assert closed_ov_ah._ah_sign(value) == expected


def test_collect_match_entries_includes_initial_and_changes_in_order():
    initial = {"id": "initial"}
    changes = [{"id": "first"}, {"id": "second"}]

    assert closed_ov_ah._collect_match_entries(
        {"initial": initial, "changes": changes}
    ) == [initial, *changes]


@pytest.mark.parametrize(
    "data",
    [{}, {"initial": None}, {"initial": {}, "changes": []}],
)
def test_collect_match_entries_handles_missing_or_empty_history(data):
    assert closed_ov_ah._collect_match_entries(data) == []


@pytest.mark.parametrize(
    ("entry_data", "expected"),
    [
        ({}, False),
        ({"time": None, "match_time": None}, False),
        ({"time": "45", "match_time": "HT"}, False),
        ({"time": "45", "match_time": "90+"}, True),
        ({"time": "FT", "match_time": "45"}, True),
    ],
)
def test_is_finished_match_handles_missing_live_and_finished_times(
    entry_data, expected
):
    assert closed_ov_ah._is_finished_match(entry_data) is expected


def test_get_last_entry_before_closed_returns_latest_open_entry_and_index():
    entries = [
        entry(over="2.5", over_odds=1.9),
        entry(over="Closed"),
        entry(over="2.5", over_odds=1.6),
        entry(over="Closed"),
    ]

    selected, index = closed_ov_ah._get_last_entry_before_closed(entries, "ov")

    assert selected is entries[2]
    assert index == 2


def test_get_last_entry_before_closed_skips_closed_over_entries():
    entries = [
        entry(over="2.5", over_odds=1.9),
        entry(over="Closed"),
        entry(over="Closed"),
    ]

    selected, index = closed_ov_ah._get_last_entry_before_closed(entries, "ov")

    assert selected is entries[0]
    assert index == 0


def test_get_last_entry_before_closed_returns_none_when_history_has_no_open_entry():
    selected, index = closed_ov_ah._get_last_entry_before_closed(
        [entry(handicap="Closed"), entry(handicap="Closed")],
        "ah",
    )

    assert selected is None
    assert index == -1


def test_notification_sent_callback_is_optional():
    assert closed_ov_ah._notification_sent_callback(None, "match-1", "ov", "League") is None


def test_notification_sent_callback_registers_message_details():
    received = []
    callback = closed_ov_ah._notification_sent_callback(
        received.append, "match-1", "ah", "League"
    )

    callback(
        42,
        "message",
        "https://example.test/match-1",
        "Handicap -0.5 Away",
        "custom-channel",
    )

    assert received == [
        {
            "match_id": "match-1",
            "market": "ah",
            "league": "League",
            "message_id": 42,
            "message": "message",
            "link": "https://example.test/match-1",
            "prediction": "Handicap -0.5 Away",
            "channel_id": "custom-channel",
        }
    ]


def test_send_over_notification_builds_payload_and_callback(monkeypatch):
    sent = []
    registered = []

    def fake_send(**kwargs):
        sent.append(kwargs)
        kwargs["on_sent_details"](7, "text", "link", "Over 2.5", "channel")

    monkeypatch.setattr(closed_ov_ah, "send_telegram_notification", fake_send)
    match = entry(
        team1="Home",
        team2="Away",
        score="1-0",
        league="League",
        match_time="30",
    )

    closed_ov_ah._send_over_notification(
        "match-1", match, "2.5", 1.55, "ODDS 1.90 -> 1.55", registered.append
    )

    assert sent[0] == {
        "league": "League",
        "team1": "Home",
        "team2": "Away",
        "score": "1-0",
        "over": "2.5",
        "over_odds": 1.55,
        "drop_type": "ODDS 1.90 -> 1.55",
        "match_id": "match-1",
        "match_time": "30",
        "on_sent_details": sent[0]["on_sent_details"],
        "strategy": "default",
    }
    assert registered[0]["market"] == "ov"
    assert registered[0]["match_id"] == "match-1"


def test_send_over_notification_uses_defaults_and_swallows_send_error(monkeypatch, caplog):
    def fail_send(**kwargs):
        raise RuntimeError("telegram unavailable")

    monkeypatch.setattr(closed_ov_ah, "send_telegram_notification", fail_send)

    closed_ov_ah._send_over_notification("match-1", {}, "2.5", 1.5, "drop")

    assert "Failed to send notification" in caplog.text


def test_send_market_notification_does_not_send_after_shutdown(monkeypatch):
    shutdown_requested = threading.Event()
    shutdown_requested.set()
    sends = []
    monkeypatch.setattr(
        closed_ov_ah,
        "send_telegram_notification",
        lambda **kwargs: sends.append(kwargs),
    )

    closed_ov_ah._send_market_notification(
        "ov",
        "match-1",
        entry(),
        "2.5",
        1.5,
        "drop",
        shutdown_requested=shutdown_requested,
    )

    assert sends == []


def over_history(confirming_odds=START_ODD, closed_odds=THRESHOLD):
    return [
        entry(over="2.5", over_odds=confirming_odds),
        entry(over="2.5", over_odds=closed_odds),
        closed_entry("ov"),
    ]


def test_find_over_pattern_sends_at_inclusive_odds_boundaries(monkeypatch):
    notifications = []
    monkeypatch.setattr(
        closed_ov_ah,
        "_send_over_notification",
        lambda *args: notifications.append(args),
    )

    matched = closed_ov_ah._find_over_pattern(
        over_history(), "match-1"
    )

    assert matched
    assert len(notifications) == 1
    assert notifications[0][2:5] == ("2.5", THRESHOLD, f"ODDS {START_ODD:.2f} -> {THRESHOLD:.2f}")


@pytest.mark.parametrize(
    ("closed_score", "confirming_score", "expected_match"),
    [
        ("2-0", "1-0", False),
        ("1-0", "1-0", True),
        ("Unknown", "1-0", True),
        ("1-0", "Unknown", True),
        (None, "1-0", True),
        ("1-0", None, True),
        ("", "1-0", True),
    ],
)
@pytest.mark.parametrize("market", ["over", "handicap"])
def test_find_pattern_compares_closed_and_confirming_scores(
    monkeypatch, market, closed_score, confirming_score, expected_match
):
    notifications = []
    if market == "over":
        monkeypatch.setattr(
            closed_ov_ah,
            "_send_over_notification",
            lambda *args: notifications.append(args),
        )
        history = [
            entry(
                over="2.5",
                over_odds=START_ODD,
                score=confirming_score,
            ),
            entry(over="2.5", over_odds=THRESHOLD),
            closed_entry("ov", score=closed_score),
        ]
        matched = closed_ov_ah._find_over_pattern(history, "match-1")
    else:
        monkeypatch.setattr(
            closed_ov_ah,
            "_send_ah_notification",
            lambda *args: notifications.append(args),
        )
        history = [
            entry(
                handicap="-0.5",
                away_odds=START_ODD,
                score=confirming_score,
            ),
            entry(handicap="-0.5", away_odds=THRESHOLD),
            closed_entry("ah", score=closed_score),
        ]
        matched = closed_ov_ah._find_ah_pattern(history, "match-1")

    assert matched is expected_match
    assert len(notifications) == int(expected_match)


@pytest.mark.parametrize("market", ["over", "handicap"])
def test_find_pattern_does_not_skip_nearest_confirming_row_on_score_mismatch(
    monkeypatch, market
):
    notifications = []
    if market == "over":
        monkeypatch.setattr(
            closed_ov_ah,
            "_send_over_notification",
            lambda *args: notifications.append(args),
        )
        history = [
            entry(over="2.5", over_odds=START_ODD, score="1-0"),
            entry(over="2.5", over_odds=1.90, score="2-0"),
            entry(over="2.5", over_odds=THRESHOLD, score="1-0"),
            closed_entry("ov", score="1-0"),
        ]
        matched = closed_ov_ah._find_over_pattern(history, "match-1")
    else:
        monkeypatch.setattr(
            closed_ov_ah,
            "_send_ah_notification",
            lambda *args: notifications.append(args),
        )
        history = [
            entry(handicap="-0.5", away_odds=START_ODD, score="1-0"),
            entry(handicap="-0.5", away_odds=1.90, score="2-0"),
            entry(handicap="-0.5", away_odds=THRESHOLD, score="1-0"),
            closed_entry("ah", score="1-0"),
        ]
        matched = closed_ov_ah._find_ah_pattern(history, "match-1")

    assert not matched
    assert notifications == []


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("time", "90"),
        ("time", "90+3"),
        ("match_time", "90+"),
        ("match_time", "90+9"),
        ("time", "FT"),
        ("match_time", "ET"),
        ("match_time", "et"),
    ],
)
@pytest.mark.parametrize("market", ["over", "handicap"])
def test_find_pattern_rejects_finished_match_times(
    monkeypatch, market, field, value
):
    notifications = []
    metadata = {field: value}
    if market == "over":
        monkeypatch.setattr(
            closed_ov_ah,
            "_send_over_notification",
            lambda *args: notifications.append(args),
        )
        history = [
            entry(over="2.5", over_odds=START_ODD, score="1-0"),
            entry(over="2.5", over_odds=THRESHOLD, score="1-0"),
            closed_entry("ov", score="1-0", **metadata),
        ]
        matched = closed_ov_ah._find_over_pattern(history, "match-1")
    else:
        monkeypatch.setattr(
            closed_ov_ah,
            "_send_ah_notification",
            lambda *args: notifications.append(args),
        )
        history = [
            entry(handicap="-0.5", away_odds=START_ODD, score="1-0"),
            entry(handicap="-0.5", away_odds=THRESHOLD, score="1-0"),
            closed_entry("ah", score="1-0", **metadata),
        ]
        matched = closed_ov_ah._find_ah_pattern(history, "match-1")

    assert not matched
    assert notifications == []


def test_find_over_pattern_returns_false_when_every_preceding_entry_is_closed():
    assert not closed_ov_ah._find_over_pattern(
        [closed_entry("ov"), closed_entry("ov")],
        "match-1",
    )


def test_find_over_pattern_matches_after_multiple_closed_snapshots(monkeypatch):
    notifications = []
    monkeypatch.setattr(
        closed_ov_ah,
        "_send_over_notification",
        lambda *args: notifications.append(args),
    )
    history = [
        entry(over="2.5", over_odds=START_ODD),
        entry(over="2.5", over_odds=THRESHOLD),
        closed_entry("ov"),
        closed_entry("ov"),
    ]

    assert closed_ov_ah._find_over_pattern(history, "match-1")
    assert len(notifications) == 1


@pytest.mark.parametrize(
    "history",
    [
        [],
        [entry(over="2.5", over_odds=1.9)],
        [entry(over="2.5", over_odds=1.9), entry(over="2.5", over_odds=1.5)],
        [closed_entry("ov"), closed_entry("ov")],
        [entry(over="2.5", over_odds="invalid"), closed_entry("ov")],
        [entry(over=None, over_odds=1.5), closed_entry("ov")],
        [entry(over="2.5", over_odds=THRESHOLD + 0.01), closed_entry("ov")],
        [
            entry(over="2.5", over_odds=START_ODD - 0.01),
            entry(over="2.5", over_odds=THRESHOLD),
            closed_entry("ov"),
        ],
        [
            entry(over="2.5", over_odds=START_ODD),
            entry(over="3.5", over_odds=THRESHOLD),
            closed_entry("ov"),
        ],
    ],
)
def test_find_over_pattern_rejects_non_matching_histories(history, monkeypatch):
    send = []
    monkeypatch.setattr(
        closed_ov_ah, "_send_over_notification", lambda *args: send.append(args)
    )

    assert not closed_ov_ah._find_over_pattern(history, "match-1")
    assert send == []


def test_find_over_pattern_skips_closed_entries_while_searching(monkeypatch):
    sent = []
    monkeypatch.setattr(
        closed_ov_ah, "_send_over_notification", lambda *args: sent.append(args)
    )
    history = [
        entry(over="2.5", over_odds=START_ODD),
        closed_entry("ov"),
        entry(over="2.5", over_odds=THRESHOLD),
        closed_entry("ov"),
    ]

    assert closed_ov_ah._find_over_pattern(history, "match-1")
    assert len(sent) == 1


@pytest.mark.parametrize(
    ("handicap", "home_odds", "away_odds", "expected_side", "expected_text"),
    [
        ("0.5", THRESHOLD, START_ODD, "home", "0.5"),
        ("-0.5", START_ODD, THRESHOLD, "away", "-0.5"),
        ("0/-0.5", START_ODD, THRESHOLD, "away", "0/-0.5"),
    ],
)
def test_find_ah_pattern_selects_odds_side_and_sends_notification(
    monkeypatch, handicap, home_odds, away_odds, expected_side, expected_text
):
    sent = []
    monkeypatch.setattr(
        closed_ov_ah, "_send_ah_notification", lambda *args: sent.append(args)
    )
    history = [
        entry(handicap=handicap, home_odds=START_ODD, away_odds=START_ODD),
        entry(handicap=handicap, home_odds=home_odds, away_odds=away_odds),
        closed_entry("ah"),
    ]

    assert closed_ov_ah._find_ah_pattern(history, "match-1")
    assert sent[0][4] == expected_side
    assert sent[0][2] == expected_text
    assert sent[0][3] == (home_odds if expected_side == "home" else away_odds)


@pytest.mark.parametrize(
    "history",
    [
        [],
        [entry(handicap="-0.5", away_odds=1.9)],
        [entry(handicap="-0.5", away_odds=1.9), entry(handicap="-0.5", away_odds=1.5)],
        [closed_entry("ah"), closed_entry("ah")],
        [entry(handicap="-0.5", away_odds="invalid"), closed_entry("ah")],
        [entry(handicap=None, away_odds=1.5), closed_entry("ah")],
        [entry(handicap="0", home_odds=1.5, away_odds=1.5), closed_entry("ah")],
        [entry(handicap="-0.5", away_odds=THRESHOLD + 0.01), closed_entry("ah")],
        [
            entry(handicap="-0.5", away_odds=START_ODD - 0.01),
            entry(handicap="-0.5", away_odds=THRESHOLD),
            closed_entry("ah"),
        ],
        [
            entry(handicap="-0.5", away_odds=START_ODD),
            entry(handicap="-1.5", away_odds=THRESHOLD),
            closed_entry("ah"),
        ],
    ],
)
def test_find_ah_pattern_rejects_non_matching_histories(history, monkeypatch):
    send = []
    monkeypatch.setattr(
        closed_ov_ah, "_send_ah_notification", lambda *args: send.append(args)
    )

    assert not closed_ov_ah._find_ah_pattern(history, "match-1")
    assert send == []


def test_send_ah_notification_formats_payload_and_callback(monkeypatch):
    sent = []
    registered = []

    def fake_send(**kwargs):
        sent.append(kwargs)
        kwargs["on_sent_details"](
            8, "text", "link", "Handicap -0.5 Home", "channel"
        )

    monkeypatch.setattr(closed_ov_ah, "send_telegram_notification", fake_send)
    match = entry(team1="Home", team2="Away", score="0-0", league="League")

    closed_ov_ah._send_ah_notification(
        "match-1", match, "0.5", 1.55, "home", "ODDS 1.90 -> 1.55", registered.append
    )

    assert sent[0]["handicap_text"] == "-0.5"
    assert sent[0]["handicap_team_order"] == "Home"
    assert sent[0]["strategy"] == "default"
    assert registered[0]["market"] == "ah"


@pytest.mark.parametrize(
    ("handicap", "expected_text", "expected_order"),
    [
        ("0.5", "-0.5", "Away"),
        ("-0.5", "-0.5", "Away"),
        ("0/-0.5", "0/-0.5", "Away"),
        (None, None, "Away"),
    ],
)
def test_send_ah_notification_formats_handicap_by_odds_side(
    monkeypatch, handicap, expected_text, expected_order
):
    sent = []
    monkeypatch.setattr(
        closed_ov_ah, "send_telegram_notification", lambda **kwargs: sent.append(kwargs)
    )

    closed_ov_ah._send_ah_notification(
        "match-1", {}, handicap, 1.5, "away", "drop"
    )

    assert sent[0]["handicap_text"] == expected_text
    assert sent[0]["handicap_team_order"] == expected_order
    assert sent[0]["team1"] == "Unknown"


def test_send_ah_notification_swallows_send_error(monkeypatch, caplog):
    def fail_send(**kwargs):
        raise RuntimeError("telegram unavailable")

    monkeypatch.setattr(closed_ov_ah, "send_telegram_notification", fail_send)

    closed_ov_ah._send_ah_notification(
        "match-1", {}, "-0.5", 1.5, "away", "drop"
    )

    assert "Failed to send notification" in caplog.text


def test_find_ah_pattern_skips_closed_entries_while_searching(monkeypatch):
    sent = []
    monkeypatch.setattr(
        closed_ov_ah, "_send_ah_notification", lambda *args: sent.append(args)
    )
    history = [
        entry(handicap="-0.5", away_odds=START_ODD),
        closed_entry("ah"),
        entry(handicap="-0.5", away_odds=THRESHOLD),
        closed_entry("ah"),
    ]

    assert closed_ov_ah._find_ah_pattern(history, "match-1")
    assert len(sent) == 1


def test_find_pattern_matches_returns_match_for_either_market(monkeypatch):
    history = {
        "over-match": {"initial": {}, "changes": []},
        "handicap-match": {"initial": {}, "changes": []},
        "unmatched": {},
    }
    monkeypatch.setattr(
        closed_ov_ah,
        "_find_over_pattern",
        lambda entries, match_id, callback: match_id == "over-match",
    )
    monkeypatch.setattr(
        closed_ov_ah,
        "_find_ah_pattern",
        lambda entries, match_id, callback: match_id == "handicap-match",
    )

    assert closed_ov_ah.find_pattern_matches(history) == [
        "over-match",
        "handicap-match",
    ]


def test_find_pattern_matches_returns_both_markets_in_order(monkeypatch):
    monkeypatch.setattr(closed_ov_ah, "_find_over_pattern", lambda *args: True)
    monkeypatch.setattr(closed_ov_ah, "_find_ah_pattern", lambda *args: True)

    assert closed_ov_ah.find_pattern_matches({"match-1": {}}) == [
        "match-1",
        "match-1",
    ]


def test_find_pattern_matches_passes_notification_callback(monkeypatch):
    observed = []
    callback = lambda *args: None
    monkeypatch.setattr(
        closed_ov_ah,
        "_find_over_pattern",
        lambda entries, match_id, on_notification_sent: observed.append(
            (entries, match_id, on_notification_sent)
        )
        or False,
    )
    monkeypatch.setattr(closed_ov_ah, "_find_ah_pattern", lambda *args: False)

    result = closed_ov_ah.find_pattern_matches(
        {"match-1": {"initial": {"value": 1}, "changes": [{"value": 2}]}},
        on_notification_sent=callback,
    )

    assert result == []
    assert observed == [
        ([{"value": 1}, {"value": 2}], "match-1", callback)
    ]


def test_find_pattern_matches_empty_history_returns_empty_list():
    assert closed_ov_ah.find_pattern_matches({}) == []


def test_find_pattern_matches_stops_before_analyzing_after_shutdown(monkeypatch):
    shutdown_requested = threading.Event()
    shutdown_requested.set()
    calls = []
    monkeypatch.setattr(
        closed_ov_ah,
        "_find_over_pattern",
        lambda *args: calls.append(args) or True,
    )

    assert closed_ov_ah.find_pattern_matches(
        {"match-1": {}}, shutdown_requested=shutdown_requested
    ) == []
    assert calls == []


def test_find_market_pattern_stops_when_shutdown_arrives_during_search(
    monkeypatch,
):
    shutdown_requested = threading.Event()
    entries = [
        *over_history()[:-1],
        entry(over="2.5", over_odds=1.5),
        closed_entry("ov"),
    ]
    status_calls = []

    def line_status(current_entry, market):
        status_calls.append(current_entry)
        if current_entry is entries[-1]:
            return "Closed"
        shutdown_requested.set()
        return "Open"

    monkeypatch.setattr(closed_ov_ah, "_line_status", line_status)
    monkeypatch.setattr(
        closed_ov_ah,
        "_get_last_entry_before_closed",
        lambda history, market: (history[-2], len(history) - 2),
    )
    monkeypatch.setattr(
        closed_ov_ah, "_get_market_values", lambda current, market: ("2.5", 1.5, None)
    )
    notifications = []

    assert not closed_ov_ah._find_market_pattern(
        entries,
        "match-1",
        "ov",
        lambda *args: notifications.append(args),
        None,
        shutdown_requested=shutdown_requested,
    )
    assert len(status_calls) == 2
    assert notifications == []
