import threading

import pytest

from app import detector
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
    metadata.setdefault("match_time", "?")
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
    metadata.setdefault("match_time", "?")
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
def test_odds_to_float(value, expected):
    assert detector._odds_to_float(value) == expected


@pytest.mark.parametrize("value", ["0", "0.0", "+0", "-0", "-0.0"])
def test_is_exact_zero_handicap_accepts_supported_zero_forms(value):
    assert detector._is_exact_zero_ah_line(value)


@pytest.mark.parametrize("value", [None, "", "0.5", "0/-0.5", "0.00"])
def test_is_exact_zero_handicap_rejects_non_matching_values(value):
    assert not detector._is_exact_zero_ah_line(value)


@pytest.mark.parametrize(
    "value",
    ["0/-0.5", "+0/-1", "0/-0,5"],
)
def test_is_ah_line_exception_accepts_supported_forms(value):
    assert detector._is_ah_line_exception(value)


@pytest.mark.parametrize(
    "value",
    [None, "", "0/0.5", "-0/-0.5", "0 / -0.5"],
)
def test_is_ah_line_exception_rejects_invalid_forms(value):
    assert not detector._is_ah_line_exception(value)


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
def test_ah_line_sign(value, expected):
    assert detector.ah_line_sign(value) == expected


def test_get_market_values_preserves_ah_line_and_selects_away_odds():
    match_entry = entry(handicap="0/-0.5", away_odds="1.8")

    assert detector._get_market_values(match_entry, "ah") == (
        "0/-0.5",
        1.8,
        "away",
    )


def test_collect_match_entries_includes_initial_and_changes_in_order():
    initial = {"id": "initial"}
    changes = [{"id": "first"}, {"id": "second"}]

    assert detector._collect_match_entries(
        {"initial": initial, "changes": changes}
    ) == [initial, *changes]


@pytest.mark.parametrize(
    "data",
    [{}, {"initial": None}, {"initial": {}, "changes": []}],
)
def test_collect_match_entries_handles_missing_or_empty_history(data):
    assert detector._collect_match_entries(data) == []


@pytest.mark.parametrize(
    ("entry_data", "expected"),
    [
        ({"match_time": "?"}, False),
        ({"match_time": "HT"}, False),
        ({"match_time": "90+"}, True),
        ({"match_time": "FT"}, True),
    ],
)
def test_is_finished_match_handles_live_and_finished_times(
    entry_data, expected
):
    assert detector._is_finished_match(entry_data) is expected


def test_get_last_entry_before_closed_returns_latest_open_entry_and_index():
    entries = [
        entry(over="2.5", over_odds=1.9),
        entry(over="Closed"),
        entry(over="2.5", over_odds=1.6),
        entry(over="Closed"),
    ]

    selected, index = detector._get_last_entry_before_closed(entries, "ov")

    assert selected is entries[2]
    assert index == 2


def test_get_last_entry_before_closed_skips_closed_over_entries():
    entries = [
        entry(over="2.5", over_odds=1.9),
        entry(over="Closed"),
        entry(over="Closed"),
    ]

    selected, index = detector._get_last_entry_before_closed(entries, "ov")

    assert selected is entries[0]
    assert index == 0


def test_get_last_entry_before_closed_returns_none_when_history_has_no_open_entry():
    selected, index = detector._get_last_entry_before_closed(
        [entry(handicap="Closed"), entry(handicap="Closed")],
        "ah",
    )

    assert selected is None
    assert index == -1


def over_history(confirming_odds=START_ODD, closed_odds=THRESHOLD):
    return [
        entry(over="2.5", over_odds=confirming_odds),
        entry(over="2.5", over_odds=closed_odds),
        closed_entry("ov"),
    ]


def test_find_over_pattern_returns_signal_at_inclusive_odds_boundaries():
    signal = detector._find_over_pattern(over_history(), "match-1")

    assert signal is not None
    assert signal["match_id"] == "match-1"
    assert signal["market"] == "ov"
    assert signal["line"] == "2.5"
    assert signal["odds"] == THRESHOLD
    assert signal["drop_type"] == f"ODDS {START_ODD:.2f} -> {THRESHOLD:.2f}"


@pytest.mark.parametrize(
    ("market", "line", "initial_odds", "latest_odds"),
    [
        ("ov", "2.5", START_ODD, THRESHOLD),
        ("ah", "-0.5", START_ODD, THRESHOLD),
    ],
)
def test_without_closed_strategy_matches_latest_open_market_snapshot(
    market, line, initial_odds, latest_odds
):
    if market == "ov":
        history = [
            entry(over=line, over_odds=initial_odds),
            entry(over=line, over_odds=latest_odds),
        ]
        signal = detector._find_over_pattern_without_closed(
            history, "match-1"
        )
    else:
        history = [
            entry(handicap=line, away_odds=initial_odds),
            entry(handicap=line, away_odds=latest_odds),
        ]
        signal = detector._find_ah_pattern_without_closed(
            history, "match-1"
        )

    assert signal is not None
    assert signal["entry"] is history[-1]
    assert signal["strategy"] == "without closed"
    assert signal["line"] == ("-0.5" if market == "ah" else line)


@pytest.mark.parametrize(
    "history",
    [
        [
            entry(over="2.5", over_odds=START_ODD),
            entry(over="2.5", over_odds=THRESHOLD + 0.01),
        ],
        [
            entry(over="2.5", over_odds=START_ODD),
            entry(over="Closed"),
        ],
        [
            entry(over="2.5", over_odds=START_ODD - 0.01),
            entry(over="2.5", over_odds=THRESHOLD),
        ],
        [
            entry(over="2.5", over_odds=START_ODD),
            entry(over="3.5", over_odds=THRESHOLD),
        ],
        [
            entry(over="2.5", over_odds=START_ODD, score="1-0"),
            entry(over="2.5", over_odds=THRESHOLD, score="2-0"),
        ],
    ],
)
def test_without_closed_strategy_rejects_non_matching_latest_snapshot(history):
    assert detector._find_over_pattern_without_closed(history, "match-1") is None


def test_find_pattern_matches_checks_without_closed_before_closed_strategy(
    monkeypatch,
):
    checks = []
    history = {"match-1": {"initial": {}, "changes": []}}

    for name in (
        "_find_over_pattern_without_closed",
        "_find_ah_pattern_without_closed",
        "_find_over_pattern",
        "_find_ah_pattern",
    ):
        monkeypatch.setattr(
            detector,
            name,
            lambda *args, _name=name, **kwargs: checks.append(_name),
        )

    assert detector.find_pattern_matches(history) == []
    assert checks == [
        "_find_over_pattern_without_closed",
        "_find_ah_pattern_without_closed",
        "_find_over_pattern",
        "_find_ah_pattern",
    ]


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
    market, closed_score, confirming_score, expected_match
):
    if market == "over":
        history = [
            entry(
                over="2.5",
                over_odds=START_ODD,
                score=confirming_score,
            ),
            entry(over="2.5", over_odds=THRESHOLD),
            closed_entry("ov", score=closed_score),
        ]
        signal = detector._find_over_pattern(history, "match-1")
    else:
        history = [
            entry(
                handicap="-0.5",
                away_odds=START_ODD,
                score=confirming_score,
            ),
            entry(handicap="-0.5", away_odds=THRESHOLD),
            closed_entry("ah", score=closed_score),
        ]
        signal = detector._find_ah_pattern(history, "match-1")

    assert (signal is not None) is expected_match


@pytest.mark.parametrize("market", ["over", "handicap"])
def test_find_pattern_does_not_skip_nearest_confirming_row_on_score_mismatch(
    market,
):
    if market == "over":
        history = [
            entry(over="2.5", over_odds=START_ODD, score="1-0"),
            entry(over="2.5", over_odds=1.90, score="2-0"),
            entry(over="2.5", over_odds=THRESHOLD, score="1-0"),
            closed_entry("ov", score="1-0"),
        ]
        signal = detector._find_over_pattern(history, "match-1")
    else:
        history = [
            entry(handicap="-0.5", away_odds=START_ODD, score="1-0"),
            entry(handicap="-0.5", away_odds=1.90, score="2-0"),
            entry(handicap="-0.5", away_odds=THRESHOLD, score="1-0"),
            closed_entry("ah", score="1-0"),
        ]
        signal = detector._find_ah_pattern(history, "match-1")

    assert signal is None


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("match_time", "90"),
        ("match_time", "90+3"),
        ("match_time", "90+"),
        ("match_time", "90+9"),
        ("match_time", "FT"),
        ("match_time", "ET"),
        ("match_time", "et"),
    ],
)
@pytest.mark.parametrize("market", ["over", "handicap"])
def test_find_pattern_rejects_finished_match_times(
    market, field, value
):
    metadata = {field: value}
    if market == "over":
        history = [
            entry(over="2.5", over_odds=START_ODD, score="1-0"),
            entry(over="2.5", over_odds=THRESHOLD, score="1-0"),
            closed_entry("ov", score="1-0", **metadata),
        ]
        signal = detector._find_over_pattern(history, "match-1")
    else:
        history = [
            entry(handicap="-0.5", away_odds=START_ODD, score="1-0"),
            entry(handicap="-0.5", away_odds=THRESHOLD, score="1-0"),
            closed_entry("ah", score="1-0", **metadata),
        ]
        signal = detector._find_ah_pattern(history, "match-1")

    assert signal is None


def test_find_over_pattern_returns_false_when_every_preceding_entry_is_closed():
    assert not detector._find_over_pattern(
        [closed_entry("ov"), closed_entry("ov")],
        "match-1",
    )


def test_find_over_pattern_matches_after_multiple_closed_snapshots():
    history = [
        entry(over="2.5", over_odds=START_ODD),
        entry(over="2.5", over_odds=THRESHOLD),
        closed_entry("ov"),
        closed_entry("ov"),
    ]

    signal = detector._find_over_pattern(history, "match-1")
    assert signal is not None
    assert signal["line"] == "2.5"


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
def test_find_over_pattern_rejects_non_matching_histories(history):
    assert detector._find_over_pattern(history, "match-1") is None


def test_find_over_pattern_skips_closed_entries_while_searching():
    history = [
        entry(over="2.5", over_odds=START_ODD),
        closed_entry("ov"),
        entry(over="2.5", over_odds=THRESHOLD),
        closed_entry("ov"),
    ]

    signal = detector._find_over_pattern(history, "match-1")
    assert signal is not None
    assert signal["odds"] == THRESHOLD


@pytest.mark.parametrize(
    ("handicap", "home_odds", "away_odds", "expected_side", "expected_text"),
    [
        ("0.5", THRESHOLD, START_ODD, "home", "0.5"),
        ("-0.5", START_ODD, THRESHOLD, "away", "-0.5"),
        ("0/-0.5", START_ODD, THRESHOLD, "away", "0/-0.5"),
    ],
)
def test_find_ah_pattern_selects_odds_side_and_returns_signal(
    handicap, home_odds, away_odds, expected_side, expected_text
):
    history = [
        entry(handicap=handicap, home_odds=START_ODD, away_odds=START_ODD),
        entry(handicap=handicap, home_odds=home_odds, away_odds=away_odds),
        closed_entry("ah"),
    ]

    signal = detector._find_ah_pattern(history, "match-1")
    assert signal is not None
    assert signal["odds_side"] == expected_side
    assert signal["line"] == expected_text
    assert signal["odds"] == (
        home_odds if expected_side == "home" else away_odds
    )


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
def test_find_ah_pattern_rejects_non_matching_histories(history):
    assert detector._find_ah_pattern(history, "match-1") is None


def test_find_ah_pattern_skips_closed_entries_while_searching():
    history = [
        entry(handicap="-0.5", away_odds=START_ODD),
        closed_entry("ah"),
        entry(handicap="-0.5", away_odds=THRESHOLD),
        closed_entry("ah"),
    ]

    signal = detector._find_ah_pattern(history, "match-1")
    assert signal is not None
    assert signal["odds_side"] == "away"


def test_find_pattern_matches_returns_signals_for_each_market():
    history = {
        "over-match": {
            "initial": entry(over="2.5", over_odds=START_ODD),
            "changes": [
                entry(over="2.5", over_odds=THRESHOLD),
                {
                    **entry(handicap="-0.5", away_odds=THRESHOLD),
                    "ov": {"over": "Closed"},
                },
            ],
        },
        "handicap-match": {
            "initial": entry(handicap="-0.5", away_odds=START_ODD),
            "changes": [
                entry(handicap="-0.5", away_odds=THRESHOLD),
                {
                    **entry(over="2.5", over_odds=THRESHOLD),
                    "ah": {"ah": "Closed"},
                },
            ],
        },
        "unmatched": {},
    }

    signals = detector.find_pattern_matches(history)

    assert [(signal["match_id"], signal["market"]) for signal in signals] == [
        ("over-match", "ov"),
        ("handicap-match", "ah"),
    ]


def test_find_pattern_matches_keeps_strategy_order_for_same_match():
    history = {
        "match-1": {
            "initial": entry(
                over="2.5",
                over_odds=START_ODD,
                handicap="-0.5",
                away_odds=START_ODD,
            ),
            "changes": [
                entry(
                    over="2.5",
                    over_odds=THRESHOLD,
                    handicap="-0.5",
                    away_odds=THRESHOLD,
                ),
                {
                    **entry(
                        handicap="-0.5",
                        away_odds=THRESHOLD,
                    ),
                    "ov": {"over": "Closed"},
                },
            ],
        }
    }

    signals = detector.find_pattern_matches(history)

    assert [(signal["market"], signal["strategy"]) for signal in signals] == [
        ("ah", "without closed"),
        ("ov", "default"),
    ]


def test_find_pattern_matches_empty_history_returns_empty_list():
    assert detector.find_pattern_matches({}) == []


def test_find_pattern_matches_stops_before_analyzing_after_shutdown(monkeypatch):
    shutdown_requested = threading.Event()
    shutdown_requested.set()
    calls = []
    monkeypatch.setattr(
        detector,
        "_find_over_pattern",
        lambda *args: calls.append(args) or True,
    )

    assert detector.find_pattern_matches(
        {"match-1": {}}, shutdown_requested=shutdown_requested
    ) == []
    assert calls == []


def test_find_market_pattern_stops_when_shutdown_arrives_during_search(monkeypatch):
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

    monkeypatch.setattr(detector, "_line_status", line_status)
    monkeypatch.setattr(
        detector,
        "_get_last_entry_before_closed",
        lambda history, market: (history[-2], len(history) - 2),
    )
    monkeypatch.setattr(
        detector, "_get_market_values", lambda current, market: ("2.5", 1.5, None)
    )
    assert detector._find_market_pattern(
        entries,
        "match-1",
        "ov",
        shutdown_requested=shutdown_requested,
    ) is None
    assert len(status_calls) == 2
