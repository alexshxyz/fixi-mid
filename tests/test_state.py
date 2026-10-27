import json

from app import state


def market_data(*, score="0-0", date="now"):
    return {
        "ah": {"ah": "0", "home_ah_odds": "1.9"},
        "ov": {"over": "2.5", "over_odds": "1.9"},
        "match_time": "12",
        "score": score,
        "date": date,
    }


def test_get_last_data_extracts_tracked_match_values():
    source = market_data()

    assert state.get_last_data(source) == {
        "ah": source["ah"],
        "ov": source["ov"],
        "match_time": "12",
        "score": "0-0",
    }
    assert state.get_last_data({"ah": {}, "ov": {}}) == {
        "ah": {},
        "ov": {},
        "match_time": "Unknown",
        "score": "Unknown",
    }


def test_load_state_from_json_returns_none_when_file_is_missing(tmp_path):
    assert state.load_state_from_json(tmp_path / "missing.json") is None


def test_load_state_from_json_loads_and_removes_file(tmp_path):
    path = tmp_path / "state.json"
    saved = {"active_match_ids": ["1"]}
    path.write_text(json.dumps(saved), encoding="utf-8")

    assert state.load_state_from_json(path) == saved
    assert not path.exists()


def test_load_state_from_json_logs_invalid_json_and_leaves_file(tmp_path, caplog):
    path = tmp_path / "state.json"
    path.write_text("{invalid", encoding="utf-8")

    assert state.load_state_from_json(path) is None
    assert path.exists()
    assert "Failed to load state" in caplog.text


def test_load_state_from_json_logs_remove_error(tmp_path, monkeypatch, caplog):
    path = tmp_path / "state.json"
    path.write_text('{"valid": true}', encoding="utf-8")
    monkeypatch.setattr(
        state.os,
        "remove",
        lambda path: (_ for _ in ()).throw(OSError("denied")),
    )

    assert state.load_state_from_json(path) is None
    assert path.exists()
    assert "Failed to load state" in caplog.text


def test_save_state_to_json_persists_all_monitor_state(tmp_path):
    path = tmp_path / "state.json"
    history = {"1": {"initial": market_data(), "changes": []}}
    last_data = {"1": {"score": "0-0"}}
    pending = {"10": {"match_id": "1"}}

    assert state.save_state_to_json(
        history, last_data, ["1"], pending, path
    )

    assert json.loads(path.read_text(encoding="utf-8")) == {
        "match_history": history,
        "last_data": last_data,
        "active_match_ids": ["1"],
        "pending_notifications": pending,
    }


def test_save_state_to_json_logs_write_error_and_returns_false(
    monkeypatch, tmp_path, caplog
):
    monkeypatch.setattr(
        "builtins.open",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            OSError("permission denied")
        ),
    )

    assert not state.save_state_to_json({}, {}, [], {}, tmp_path / "state.json")
    assert "Failed to save state" in caplog.text


def test_restore_state_backfills_dates_score_and_deduplicates_match_ids():
    initial = market_data(date="original")
    latest = market_data(score="2-1", date="changed")
    saved = {
        "active_match_ids": ["1", "1"],
        "match_history": {
            "1": {"initial": initial, "changes": [latest]},
        },
        "last_data": {"1": {"ah": initial["ah"], "ov": initial["ov"]}},
        "pending_notifications": {"10": {"match_id": "1"}},
    }

    restored = state.restore_state(saved)

    assert restored == {
        "match_history": saved["match_history"],
        "last_data": {"1": {**saved["last_data"]["1"], "score": "2-1"}},
        "pending_notifications": saved["pending_notifications"],
        "active_match_ids": ["1"],
    }
    assert initial["date"] == "original"
    assert latest["date"] == "changed"


def test_restore_state_backfills_missing_dates_and_returns_none_for_empty_state():
    initial = {"ah": {}, "ov": {}}
    saved = {
        "match_history": {"1": {"initial": initial, "changes": [None]}},
    }

    restored = state.restore_state(saved)

    assert initial["date"] is None
    assert restored["active_match_ids"] == ["1"]
    assert restored["last_data"] == {}
    assert restored["pending_notifications"] == {}
    assert state.restore_state({}) is None


def test_restore_state_preserves_existing_score_and_does_not_create_missing_last_data():
    initial = market_data(score="0-0")
    saved = {
        "match_history": {
            "present": {"initial": initial, "changes": []},
            "missing": {"initial": initial.copy(), "changes": []},
        },
        "last_data": {"present": {"score": "1-0"}},
    }

    restored = state.restore_state(saved)

    assert restored["last_data"] == {"present": {"score": "1-0"}}
