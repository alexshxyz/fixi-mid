import json

import pytest

import storage
from config import CHANNEL_ID


@pytest.fixture
def matches_file(tmp_path, monkeypatch):
    path = tmp_path / "nested" / "matches.json"
    monkeypatch.setattr(storage, "MATCHES_FILE", str(path))
    return path


def read_matches(path):
    return json.loads(path.read_text(encoding="utf-8"))


def sample_match(**overrides):
    match = {
        "league": "League",
        "home_team": "Home",
        "away_team": "Away",
        "prediction": "Over 2.5",
        "odds": 1.9,
        "link": "https://example.test/match/123",
        "final_score": "2-1",
        "result": "Won",
        "date": "2026-10-09",
        "channel_id": "channel-a",
    }
    match.update(overrides)
    return match


def test_ensure_matches_file_creates_parent_and_empty_json(matches_file):
    result = storage._ensure_matches_file()

    assert result == matches_file
    assert matches_file.read_text(encoding="utf-8") == "[]\n"


def test_ensure_matches_file_does_not_overwrite_existing_data(matches_file):
    matches_file.parent.mkdir(parents=True)
    matches_file.write_text('[{"id": 1}]', encoding="utf-8")

    storage._ensure_matches_file()

    assert read_matches(matches_file) == [{"id": 1}]


def test_load_matches_returns_saved_list(matches_file):
    matches_file.parent.mkdir(parents=True)
    matches_file.write_text('[{"match_id": "123"}]', encoding="utf-8")

    assert storage._load_matches() == [{"match_id": "123"}]


@pytest.mark.parametrize(
    ("contents", "logs_warning"),
    [
        ("{broken json", True),
        ('{"not": "a list"}', False),
        ("null", False),
        ("", True),
    ],
)
def test_load_matches_resets_invalid_or_non_list_json(
    matches_file, contents, logs_warning, caplog
):
    matches_file.parent.mkdir(parents=True)
    matches_file.write_text(contents, encoding="utf-8")

    assert storage._load_matches() == []
    assert read_matches(matches_file) == []
    assert ("Resetting" in caplog.text) is logs_warning


def test_save_matches_writes_json_atomically_and_cleans_up_temp_files(matches_file):
    storage._save_matches([{"team": "Команда", "odds": 1.75}])

    assert read_matches(matches_file) == [{"team": "Команда", "odds": 1.75}]
    assert matches_file.read_text(encoding="utf-8").endswith("\n")
    assert list(matches_file.parent.glob("*.tmp")) == []


def test_save_matches_preserves_existing_file_and_removes_temp_file_on_failure(
    matches_file, monkeypatch
):
    matches_file.parent.mkdir(parents=True)
    matches_file.write_text('[{"existing": true}]', encoding="utf-8")

    def fail_dump(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(storage.json, "dump", fail_dump)

    with pytest.raises(OSError, match="disk full"):
        storage._save_matches([{"replacement": True}])

    assert matches_file.read_text(encoding="utf-8") == '[{"existing": true}]'
    assert list(matches_file.parent.iterdir()) == [matches_file]


def test_init_storage_creates_file_and_returns_true(matches_file):
    assert storage.init_storage() is True
    assert read_matches(matches_file) == []


def test_check_duplicate_match_matches_link_prediction_and_channel(matches_file):
    storage._save_matches(
        [sample_match(), sample_match(channel_id="channel-b")]
    )

    assert storage.check_duplicate_match(
        "https://example.test/match/123", "Over 2.5", "channel-a"
    )
    assert storage.check_duplicate_match(
        "https://example.test/match/123", "Over 2.5", "channel-b"
    )
    assert not storage.check_duplicate_match(
        "https://example.test/match/123", "Over 2.5", "channel-c"
    )
    assert not storage.check_duplicate_match(
        "https://example.test/match/123", "Handicap -0.5 Home", "channel-a"
    )


@pytest.mark.parametrize(
    ("link", "prediction"),
    [(None, "Over 2.5"), ("", "Over 2.5"), ("link", None), ("link", "")],
)
def test_check_duplicate_match_rejects_empty_link_or_prediction(
    matches_file, link, prediction
):
    storage._save_matches([sample_match(link=link, prediction=prediction)])

    assert not storage.check_duplicate_match(link, prediction, "channel-a")


def test_check_duplicate_match_uses_default_channel_for_legacy_records(matches_file):
    storage._save_matches(
        [{"link": "link", "prediction": "Over 2.5"}]
    )

    assert storage.check_duplicate_match("link", "Over 2.5", CHANNEL_ID)
    assert not storage.check_duplicate_match("link", "Over 2.5", "other-channel")


@pytest.mark.parametrize(
    ("item", "expected"),
    [
        ({"match_id": 123, "link": "https://site/match/999"}, "123"),
        ({"match_id": 0}, "0"),
        ({"link": "https://site/match/abc%20123"}, "abc 123"),
        ({"link": "https://site/match/123/"}, "123"),
        ({"link": "https://site/"}, None),
        ({}, None),
        ({"link": ""}, None),
    ],
)
def test_get_match_id(item, expected):
    assert storage._get_match_id(item) == expected


@pytest.mark.parametrize(
    ("item", "expected"),
    [
        ({"market": "ov", "prediction": "Handicap -0.5 Home"}, "ov"),
        ({"market": "ah"}, "ah"),
        ({"market": "unknown", "prediction": "Over 2.5"}, "ov"),
        ({"prediction": "Handicap -0.5 Away"}, "ah"),
        ({"prediction": "Other prediction"}, None),
        ({}, None),
    ],
)
def test_get_market(item, expected):
    assert storage._get_market(item) == expected


@pytest.mark.parametrize(
    ("item", "expected"),
    [
        ({"strategy": "new", "drop_type": "ODDS x"}, "new"),
        ({"strategy": "old", "drop_type": "LINE x"}, "old"),
        ({"strategy": "invalid", "drop_type": "LINE x"}, "new"),
        ({"drop_type": "LINE 1.9 -> 1.5"}, "new"),
        ({"drop_type": "ODDS 1.9 -> 1.5"}, "old"),
        ({}, "old"),
    ],
)
def test_get_strategy(item, expected):
    assert storage._get_strategy(item) == expected


def test_get_match_notification_states_combines_markets_and_legacy_strategies(
    matches_file,
):
    storage._save_matches(
        [
            sample_match(match_id=123, strategy="old", market="ov"),
            sample_match(
                match_id=None,
                link="https://example.test/match/123",
                prediction="Handicap -0.5 Away",
                strategy="new",
                drop_type="LINE 1.9 -> 1.5",
                market=None,
            ),
            sample_match(
                match_id="456",
                link="",
                prediction="Over 2.5",
                strategy=None,
                drop_type="LINE 2.0 -> 1.5",
                market="invalid",
            ),
            "not a record",
            {"link": "", "prediction": "Over 2.5"},
        ]
    )

    assert storage.get_match_notification_states() == {
        "123": {"sent_markets": {"ov", "ah"}, "strategies": {"old", "new"}},
        "456": {"sent_markets": {"ov"}, "strategies": {"new"}},
    }


def test_get_match_notification_state_stringifies_id_and_returns_fresh_empty_state(
    matches_file,
):
    empty_state = storage.get_match_notification_state(999)

    assert empty_state == {"sent_markets": set(), "strategies": set()}
    empty_state["sent_markets"].add("ov")
    assert storage.get_match_notification_state("999") == {
        "sent_markets": set(),
        "strategies": set(),
    }


def test_check_duplicate_market_uses_derived_notification_state(matches_file):
    storage._save_matches([sample_match(match_id=123, market="ov")])

    assert storage.check_duplicate_market(123, "ov")
    assert not storage.check_duplicate_market(123, "ah")


def test_update_match_mark_updates_matching_record_and_preserves_other_fields(
    matches_file,
):
    original = sample_match(mark="old", extra="kept")
    storage._save_matches([original, sample_match(link="another-link")])

    assert storage.update_match_mark(
        original["link"], original["prediction"], "👍", "channel-a"
    )

    updated = read_matches(matches_file)
    assert updated[0] == {**original, "mark": "👍"}
    assert list(updated[0])[0] == "mark"
    assert updated[1]["link"] == "another-link"


@pytest.mark.parametrize(
    ("link", "prediction"),
    [(None, "Over 2.5"), ("link", None), ("", "Over 2.5"), ("link", "")],
)
def test_update_match_mark_rejects_missing_link_or_prediction(
    matches_file, link, prediction
):
    assert not storage.update_match_mark(link, prediction, "👍")
    assert not matches_file.exists()


def test_update_match_mark_skips_non_dict_and_returns_false_when_not_found(
    matches_file, caplog
):
    storage._save_matches(["not a record", sample_match()])

    assert not storage.update_match_mark("missing", "Over 2.5", "👍", "channel-a")
    assert "Could not find notification" in caplog.text
    assert read_matches(matches_file)[0] == "not a record"


def test_update_match_mark_handles_save_failure(matches_file, monkeypatch, caplog):
    storage._save_matches([sample_match()])

    def fail_save(matches):
        raise OSError("disk full")

    monkeypatch.setattr(storage, "_save_matches", fail_save)

    assert not storage.update_match_mark(
        "https://example.test/match/123", "Over 2.5", "👍", "channel-a"
    )
    assert "Failed to update notification mark" in caplog.text


def test_update_match_mark_uses_default_channel_for_legacy_records(matches_file):
    storage._save_matches(
        [{"link": "link", "prediction": "Over 2.5", "mark": None}]
    )

    assert storage.update_match_mark("link", "Over 2.5", "✅", CHANNEL_ID)
    assert read_matches(matches_file)[0]["mark"] == "✅"
    assert not storage.update_match_mark("link", "Over 2.5", "❌", "other-channel")


def test_save_match_persists_all_fields_and_returns_one_based_row_order(
    matches_file, monkeypatch
):
    monkeypatch.setattr(storage, "CHANNEL_ID", "default-channel")

    assert storage.save_match(
        "League",
        "Home",
        "Away",
        "Over 2.5",
        "1.95",
        "link",
        final_score="2-1",
        result="Won",
        date_value="2026-10-09",
        channel_id=None,
        drop_type="ODDS 1.9 -> 1.5",
        match_id=123,
        market="ov",
        strategy="old",
    ) == (1, 1)
    assert storage.save_match(
        "League", "Home 2", "Away 2", "Over 3.5", 2.0, "link-2"
    ) == (2, 2)

    first, second = read_matches(matches_file)
    assert first == {
        "mark": None,
        "league": "League",
        "home_team": "Home",
        "away_team": "Away",
        "prediction": "Over 2.5",
        "drop_type": "ODDS 1.9 -> 1.5",
        "odds": 1.95,
        "final_score": "2-1",
        "result": "Won",
        "link": "link",
        "match_id": "123",
        "market": "ov",
        "strategy": "old",
        "date": "2026-10-09",
        "source": "Crown",
        "channel_id": "default-channel",
    }
    assert second["match_id"] is None
    assert second["date"] == storage.date.today().isoformat()


@pytest.mark.parametrize(
    ("odds", "expected"),
    [(None, None), ("invalid", None), (object(), None), ("2.05", 2.05)],
)
def test_save_match_normalizes_optional_or_invalid_odds(
    matches_file, odds, expected
):
    storage.save_match("League", "Home", "Away", "Over 2.5", odds, "link")

    assert read_matches(matches_file)[0]["odds"] == expected


def test_save_match_does_not_add_duplicate_in_same_channel(matches_file):
    arguments = ("League", "Home", "Away", "Over 2.5", 1.9, "link")

    assert storage.save_match(*arguments, channel_id="channel-a") == (1, 1)
    assert storage.save_match(*arguments, channel_id="channel-a") == (None, None)
    assert len(read_matches(matches_file)) == 1


def test_save_match_allows_same_prediction_in_another_channel(matches_file):
    arguments = ("League", "Home", "Away", "Over 2.5", 1.9, "link")

    assert storage.save_match(*arguments, channel_id="channel-a") == (1, 1)
    assert storage.save_match(*arguments, channel_id="channel-b") == (2, 2)
    assert len(read_matches(matches_file)) == 2


def test_get_all_matches_returns_persisted_rows(matches_file):
    records = [sample_match(), sample_match(link="link-2")]
    storage._save_matches(records)

    assert storage.get_all_matches() == records


def test_get_matches_in_date_range_includes_start_and_excludes_end(matches_file):
    records = [
        sample_match(date="2026-10-01"),
        sample_match(date="2026-10-05"),
        sample_match(date="2026-10-10"),
        sample_match(date=None),
    ]
    storage._save_matches(records)

    assert storage.get_matches_in_date_range("2026-10-05", "2026-10-10") == [
        records[1]
    ]


def test_get_matches_in_date_range_returns_empty_for_no_matches(matches_file):
    storage._save_matches([sample_match(date=None)])

    assert storage.get_matches_in_date_range("2026-10-01", "2026-10-10") == []


@pytest.mark.parametrize(
    ("results", "expected"),
    [
        ([], (0, 0, 0, 0)),
        (
            ["Won", "Lost", "Void", "Pending", None],
            (5, 1, 1, 1),
        ),
    ],
)
def test_calculate_stats(results, expected):
    assert storage.calculate_stats([{"result": result} for result in results]) == expected


def test_get_stats_by_league_counts_results_and_uses_unknown_league():
    records = [
        {"league": "League A", "result": "Won"},
        {"league": "League A", "result": "Lost"},
        {"league": "League A", "result": "Void"},
        {"league": "League A", "result": "Pending"},
        {"result": "Won"},
        {"league": "League B", "result": None},
    ]

    assert storage.get_stats_by_league(records) == {
        "League A": {"total": 4, "wins": 1, "losses": 1, "voids": 1},
        "Unknown": {"total": 1, "wins": 1, "losses": 0, "voids": 0},
        "League B": {"total": 1, "wins": 0, "losses": 0, "voids": 0},
    }
