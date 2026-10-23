from datetime import date, datetime

import pytest

from tools import stats_manual


def test_get_full_stats_formats_rates_and_profit(monkeypatch):
    monkeypatch.setattr(stats_manual, "get_all_matches", lambda: ["data"])
    monkeypatch.setattr(
        stats_manual, "calculate_stats", lambda matches: (4, 2, 1, 1)
    )

    assert stats_manual.get_full_stats() == (
        "📚 SUMMARY\n\n"
        "💰 15% ROI 📈 66% WR\n\n"
        "4 matches (2W / 1L / 1D)"
    )


def test_get_full_stats_handles_empty_data_and_all_void_matches(monkeypatch):
    monkeypatch.setattr(stats_manual, "get_all_matches", lambda: [])
    monkeypatch.setattr(stats_manual, "calculate_stats", lambda matches: (0, 0, 0, 0))
    assert stats_manual.get_full_stats() == "📚 SUMMARY\n\nNo data available."

    monkeypatch.setattr(stats_manual, "calculate_stats", lambda matches: (2, 0, 0, 2))
    assert stats_manual.get_full_stats() == (
        "📚 SUMMARY\n\n"
        "💰 0% ROI 📈 0% WR\n\n"
        "2 matches (0W / 0L / 2D)"
    )

    monkeypatch.setattr(stats_manual, "calculate_stats", lambda matches: (2, 0, 2, 0))
    assert "💰 -100% ROI 📈 0% WR" in stats_manual.get_full_stats()


def test_get_full_stats_reports_storage_errors(monkeypatch, capsys):
    monkeypatch.setattr(
        stats_manual,
        "get_all_matches",
        lambda: (_ for _ in ()).throw(OSError("unavailable")),
    )

    assert stats_manual.get_full_stats() == (
        "❌ Ошибка при получении статистики: unavailable"
    )
    assert "Ошибка при получении полной статистики: unavailable" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("function", "expected_order", "header"),
    [
        (stats_manual.get_top_leagues, ["Best", "Extra", "Middle"], "🏆 TOP LEAGUES"),
        (stats_manual.get_worst_leagues, ["Worst", "Third", "Middle"], "⛔️ WORST LEAGUES"),
    ],
)
def test_league_summaries_sort_and_limit_to_three(
    monkeypatch, function, expected_order, header
):
    league_data = {
        "Middle": {"wins": 2, "losses": 1, "voids": 0},
        "Worst": {"wins": 0, "losses": 4, "voids": 1},
        "Best": {"wins": 5, "losses": 0, "voids": 0},
        "Third": {"wins": 1, "losses": 1, "voids": 0},
        "Extra": {"wins": 3, "losses": 2, "voids": 0},
    }
    monkeypatch.setattr(stats_manual, "get_all_matches", lambda: [])
    monkeypatch.setattr(stats_manual, "get_stats_by_league", lambda matches: league_data)

    result = function()

    assert result.startswith(header)
    positions = [result.index(league) for league in expected_order]
    assert positions == sorted(positions)
    assert ("Third" not in result) is (function is stats_manual.get_top_leagues)
    assert ("Extra" not in result) is (function is stats_manual.get_worst_leagues)


@pytest.mark.parametrize(
    ("function", "expected"),
    [
        (stats_manual.get_top_leagues, "🏆 TOP LEAGUES\n\nNo data available."),
        (stats_manual.get_worst_leagues, "⛔️ WORST LEAGUES\n\nNo data available."),
    ],
)
def test_league_summaries_handle_empty_data_and_errors(
    monkeypatch, function, expected, capsys
):
    monkeypatch.setattr(stats_manual, "get_all_matches", lambda: [])
    monkeypatch.setattr(stats_manual, "get_stats_by_league", lambda matches: {})
    assert function() == expected

    monkeypatch.setattr(
        stats_manual,
        "get_all_matches",
        lambda: (_ for _ in ()).throw(OSError("unavailable")),
    )
    assert function() == "❌ Ошибка при получении статистики: unavailable"
    assert "Ошибка при получении" in capsys.readouterr().out


@pytest.mark.parametrize(
    "function",
    [stats_manual.get_top_leagues, stats_manual.get_worst_leagues],
)
def test_league_summaries_use_zero_win_rate_for_void_only_league(
    monkeypatch, function
):
    monkeypatch.setattr(stats_manual, "get_all_matches", lambda: [])
    monkeypatch.setattr(
        stats_manual,
        "get_stats_by_league",
        lambda matches: {
            "Void league": {"wins": 0, "losses": 0, "voids": 2}
        },
    )

    assert "0W / 0L / 2D — 0%" in function()


def test_get_last_5_matches_returns_newest_five_with_result_emojis(monkeypatch):
    matches = [
        {
            "league": f"League {index}",
            "home_team": f"Home {index}",
            "away_team": f"Away {index}",
            "prediction": f"Pick {index}",
            "final_score": f"{index}-0",
            "result": result,
            "date": "2026-10-23",
            "link": f"https://example.test/{index}",
            "odds": 1.5,
        }
        for index, result in enumerate(["Won", "Lost", "Void", "Unknown", "Won", "Lost"])
    ]
    monkeypatch.setattr(stats_manual, "get_all_matches", lambda: matches)

    result = stats_manual.get_last_5_matches()

    assert [match["league"] for match in result] == [
        "League 5",
        "League 4",
        "League 3",
        "League 2",
        "League 1",
    ]
    assert [match["result_emoji"] for match in result] == ["❌", "✅", "?", "🔁", "❌"]


def test_get_last_5_matches_handles_empty_missing_fields_and_errors(
    monkeypatch, capsys
):
    monkeypatch.setattr(stats_manual, "get_all_matches", lambda: [])
    assert stats_manual.get_last_5_matches() == []

    monkeypatch.setattr(stats_manual, "get_all_matches", lambda: [{}])
    assert stats_manual.get_last_5_matches() == [
        {
            "league": "?",
            "home_team": "?",
            "away_team": "?",
            "prediction": "?",
            "final_score": "?",
            "result": "?",
            "result_emoji": "?",
            "date": None,
            "link": "#",
            "odds": None,
        }
    ]

    monkeypatch.setattr(
        stats_manual,
        "get_all_matches",
        lambda: (_ for _ in ()).throw(OSError("unavailable")),
    )
    assert stats_manual.get_last_5_matches() == []
    assert "Ошибка при получении последних матчей: unavailable" in capsys.readouterr().out


def test_format_last_5_matches_handles_empty_and_populated_data():
    assert stats_manual.format_last_5_matches([]) == (
        "🕒 LAST 5 MATCHES\n\nNo recent matches available."
    )

    result = stats_manual.format_last_5_matches(
        [
            {
                "result_emoji": "✅",
                "league": "League",
                "home_team": "Home",
                "away_team": "Away",
                "prediction": "Over 2.5",
                "final_score": "2-1",
                "date": "2026-10-23",
                "odds": 1.9,
            }
        ]
    )
    assert result == (
        "🕒 LAST 5 MATCHES\n\n"
        "✅ League\n"
        "Home vs Away\n"
        "Prediction: Over 2.5\n"
        "Score: 2-1\n"
        "Date: 2026-10-23\n"
        "Odds: 1.9"
    )


@pytest.mark.parametrize(
    ("index", "match", "expected_date", "expected_emoji"),
    [
        (0, {"date": "2026-10-23", "result": "Won"}, "23.10.26", "✅"),
        (1, {"date": date(2026, 1, 2), "result": "Void"}, "02.01.26", "🔁"),
        (0, {"date": "2026-10-23", "result": "Lost"}, "23.10.26", "❌"),
        (0, {"date": "invalid", "result": "Unknown"}, "?", "?"),
        (0, {}, "?", "?"),
    ],
)
def test_get_match_details_formats_and_defaults_data(
    monkeypatch, index, match, expected_date, expected_emoji
):
    matches = [{}, match] if index == 0 else [match, {}]
    monkeypatch.setattr(stats_manual, "get_all_matches", lambda: matches)

    result = stats_manual.get_match_details_by_index(index)

    assert result["date"] == expected_date
    assert result["emoji"] == expected_emoji
    assert result["league"] == (match.get("league") or "?")
    assert result["link"] == (match.get("link") or "#")


@pytest.mark.parametrize("index", [-1, 2])
def test_get_match_details_rejects_out_of_range_indexes(monkeypatch, index):
    monkeypatch.setattr(stats_manual, "get_all_matches", lambda: [{}, {}])

    assert stats_manual.get_match_details_by_index(index) is None


def test_get_match_details_reports_storage_errors(monkeypatch, capsys):
    monkeypatch.setattr(
        stats_manual,
        "get_all_matches",
        lambda: (_ for _ in ()).throw(OSError("unavailable")),
    )

    assert stats_manual.get_match_details_by_index(0) is None
    assert "Ошибка при получении деталей матча: unavailable" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("now", "expected_start", "expected_end", "expected_month"),
    [
        (datetime(2026, 10, 23), "2026-10-01", "2026-11-01", "OCTOBER"),
        (datetime(2026, 12, 31), "2026-12-01", "2027-01-01", "DECEMBER"),
    ],
)
def test_get_this_month_stats_uses_half_open_month_range(
    monkeypatch, now, expected_start, expected_end, expected_month
):
    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return now

    range_calls = []
    monkeypatch.setattr(stats_manual, "datetime", FixedDateTime)
    monkeypatch.setattr(
        stats_manual,
        "get_matches_in_date_range",
        lambda start, end: range_calls.append((start, end)) or ["matches"],
    )
    monkeypatch.setattr(
        stats_manual, "calculate_stats", lambda matches: (3, 2, 1, 0)
    )

    result = stats_manual.get_this_month_stats()

    assert range_calls == [(expected_start, expected_end)]
    assert result == (
        f"📅 {expected_month}\n\n"
        "💰 +0.60 units 📈 66% WR\n\n"
        "🎯 3 matches\n"
        "2W / 1L / 0D"
    )


def test_get_this_month_stats_handles_void_only_empty_and_error_cases(
    monkeypatch, capsys
):
    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 1, 15)

    monkeypatch.setattr(stats_manual, "datetime", FixedDateTime)
    monkeypatch.setattr(
        stats_manual, "get_matches_in_date_range", lambda start, end: []
    )
    monkeypatch.setattr(
        stats_manual, "calculate_stats", lambda matches: (2, 0, 0, 2)
    )
    assert stats_manual.get_this_month_stats() == (
        "📅 JANUARY\n\n"
        "💰 +0.00 units 📈 0% WR\n\n"
        "🎯 2 matches\n"
        "0W / 0L / 2D"
    )

    monkeypatch.setattr(
        stats_manual, "calculate_stats", lambda matches: (2, 0, 2, 0)
    )
    assert "💰 -2.00 units 📈 0% WR" in stats_manual.get_this_month_stats()

    monkeypatch.setattr(
        stats_manual, "calculate_stats", lambda matches: (0, 0, 0, 0)
    )
    assert stats_manual.get_this_month_stats() == (
        "📅 JANUARY\n\nNo data available."
    )

    monkeypatch.setattr(
        stats_manual,
        "get_matches_in_date_range",
        lambda *args: (_ for _ in ()).throw(OSError("unavailable")),
    )
    assert stats_manual.get_this_month_stats() == (
        "❌ Ошибка при получении статистики: unavailable"
    )
    assert "Ошибка при получении статистики за месяц: unavailable" in capsys.readouterr().out
