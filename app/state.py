import json
import logging
import os
from typing import Any

from .config import STATE_SAVE_FILE
from .logger import setup_logger
from .match_types import MatchHistoryMap, MatchSnapshot

logger: logging.Logger = setup_logger(__name__)


def get_last_data(match_data: MatchSnapshot) -> MatchSnapshot:
    """Извлечь из снимка матчевые данные, нужные для сравнения."""
    return {
        'ah': match_data['ah'],
        'ov': match_data['ov'],
        'match_time': match_data.get('match_time', 'Unknown'),
        'score': match_data.get('score', 'Unknown'),
    }


def load_state_from_json(
    path: str | os.PathLike[str] = STATE_SAVE_FILE,
) -> Any:
    """Загрузить сохранённое состояние матча из JSON-файла."""
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            saved_state = json.load(f)
        logger.info(f"Loaded saved state file {path}")
        return saved_state
    except Exception as e:
        logger.error(f"Failed to load state from {path}: {e}")
        return None


def remove_state_file(
    path: str | os.PathLike[str] = STATE_SAVE_FILE,
) -> bool:
    """Удалить файл состояния после его успешного восстановления."""
    try:
        os.remove(path)
        logger.info(f"Removed saved state file {path}")
        return True
    except Exception as e:
        logger.error(f"Failed to remove saved state file {path}: {e}")
        return False


def save_state_to_json(
    match_history: MatchHistoryMap,
    last_data: dict[str, MatchSnapshot],
    active_match_ids: list[str],
    pending_notifications: dict[str, dict[str, Any]],
    path: str | os.PathLike[str] = STATE_SAVE_FILE,
) -> bool:
    """Сохранить текущее состояние матчей в JSON-файл."""
    try:
        payload = {
            "match_history": match_history,
            "last_data": last_data,
            "active_match_ids": active_match_ids,
            "pending_notifications": pending_notifications,
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        logger.info(f"Saved state to {path}")
        return True
    except Exception as e:
        logger.error(f"Failed to save state to {path}: {e}")
        return False


def restore_state(
    saved_state: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Подготовить сохранённое состояние к использованию монитором."""
    if not saved_state:
        return None

    match_history = saved_state.get("match_history", {})
    for match_data in match_history.values():
        entries = [match_data.get("initial"), *match_data.get("changes", [])]
        for entry in entries:
            if entry is not None:
                entry.setdefault("date", None)

    last_data = saved_state.get("last_data", {})
    for match_id, match_data in match_history.items():
        match_last_data = last_data.get(match_id)
        if match_last_data is None or 'score' in match_last_data:
            continue
        changes = match_data.get('changes', [])
        latest_entry = changes[-1] if changes else match_data.get('initial', {})
        if 'score' in latest_entry:
            match_last_data['score'] = latest_entry['score']

    active_match_ids = list(dict.fromkeys(
        [
            *saved_state.get("active_match_ids", []),
            *match_history.keys(),
        ]
    ))
    return {
        "match_history": match_history,
        "last_data": last_data,
        "pending_notifications": saved_state.get("pending_notifications", {}),
        "active_match_ids": active_match_ids,
    }
