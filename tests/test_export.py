import csv
import json

import pytest

from app.tools import export


@pytest.fixture
def matches_path(tmp_path, monkeypatch):
    path = tmp_path / "matches.json"
    monkeypatch.setenv("MATCHES_FILE", str(path))
    return path


def test_export_matches_writes_preferred_columns_and_ignores_extra_fields(
    matches_path,
):
    matches_path.write_text(
        json.dumps(
            [
                {
                    "date": "2026-10-23",
                    "league": "League",
                    "home_team": "Home",
                    "away_team": "Away",
                    "prediction": "Over 2.5",
                    "odds": 1.9,
                    "extra": "ignored",
                },
                {"league": "Another league", "result": "Won"},
            ]
        ),
        encoding="utf-8",
    )

    count, csv_path = export.export_matches_to_csv()

    with csv_path.open("r", encoding="utf-8-sig", newline="") as csv_file:
        rows = list(csv.DictReader(csv_file, delimiter=";"))

    assert count == 2
    assert csv_path == matches_path.with_suffix(".csv")
    assert rows == [
        {
            "date": "2026-10-23",
            "mark": "",
            "league": "League",
            "home_team": "Home",
            "away_team": "Away",
            "prediction": "Over 2.5",
            "odds": "1.9",
            "final_score": "",
            "result": "",
            "link": "",
            "source": "",
        },
        {
            "date": "",
            "mark": "",
            "league": "Another league",
            "home_team": "",
            "away_team": "",
            "prediction": "",
            "odds": "",
            "final_score": "",
            "result": "Won",
            "link": "",
            "source": "",
        },
    ]


def test_export_empty_list_writes_headers_only(matches_path):
    matches_path.write_text("[]", encoding="utf-8")

    count, csv_path = export.export_matches_to_csv()

    with csv_path.open("r", encoding="utf-8-sig", newline="") as csv_file:
        rows = list(csv.reader(csv_file, delimiter=";"))

    assert count == 0
    assert rows == [list(export.PREFERRED_COLUMNS)]


def test_export_resolves_relative_match_path_from_current_directory(
    tmp_path, monkeypatch
):
    monkeypatch.delenv("MATCHES_FILE", raising=False)
    monkeypatch.setenv("MATCHES_FILE", "nested/matches.json")
    monkeypatch.chdir(tmp_path)
    relative_path = tmp_path / "nested" / "matches.json"
    relative_path.parent.mkdir()
    relative_path.write_text("[]", encoding="utf-8")

    count, csv_path = export.export_matches_to_csv()

    assert count == 0
    assert csv_path == relative_path.with_suffix(".csv")


def test_export_defaults_to_data_directory(tmp_path, monkeypatch):
    monkeypatch.delenv("MATCHES_FILE", raising=False)
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    matches_path = data_dir / "matches.json"
    matches_path.write_text("[]", encoding="utf-8")
    monkeypatch.setattr(export, "PROJECT_DIR", tmp_path)

    count, csv_path = export.export_matches_to_csv()

    assert count == 0
    assert csv_path == data_dir / "matches.csv"


@pytest.mark.parametrize("contents", ['{"match": 1}', "null", "{}", '[1, "invalid"]'])
def test_export_rejects_non_list_or_non_object_json(matches_path, contents):
    matches_path.write_text(contents, encoding="utf-8")

    with pytest.raises(ValueError, match="Expected a JSON list of match objects"):
        export.export_matches_to_csv()


def test_export_propagates_invalid_json_and_missing_file_errors(
    matches_path, tmp_path, monkeypatch
):
    matches_path.write_text("{invalid", encoding="utf-8")
    with pytest.raises(json.JSONDecodeError):
        export.export_matches_to_csv()

    missing_path = tmp_path / "missing.json"
    monkeypatch.setenv("MATCHES_FILE", str(missing_path))
    with pytest.raises(FileNotFoundError):
        export.export_matches_to_csv()


def test_export_main_prints_success_message(matches_path, capsys):
    matches_path.write_text("[]", encoding="utf-8")

    export.main()

    assert capsys.readouterr().out == (
        f"Exported 0 matches to {matches_path.with_suffix('.csv')}\n"
    )


@pytest.mark.parametrize(
    "contents",
    ["{invalid", '{"matches": []}'],
)
def test_export_main_reports_input_errors_as_system_exit(
    matches_path, contents
):
    matches_path.write_text(contents, encoding="utf-8")

    with pytest.raises(SystemExit, match="Could not export matches:"):
        export.main()
