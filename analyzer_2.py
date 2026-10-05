import json
import math
import os
from datetime import datetime, timedelta

from config import (
    DEBUGMODE,
    OVER_TOTAL_DROP_MAX,
    OVER_TOTAL_DROP_THRESHOLD,
    SKIPMATCH,
)
from logger import setup_logger
from notifier import send_telegram_notification

logger = setup_logger(__name__)


# Преобразует значение коэффициента в число.
def _to_float(value):
    try:
        return float(value)
    except (ValueError, TypeError):
        return None


# Преобразует тотал, включая азиатский формат вида «2/2.5», в число.
def _to_total(value):
    if value is None:
        return None
    text = str(value).strip().replace(",", ".")
    try:
        parts = text.split("/")
        if len(parts) == 1:
            total = float(parts[0])
        elif len(parts) == 2:
            total = (float(parts[0].strip()) + float(parts[1].strip())) / 2
        else:
            return None
    except (ValueError, TypeError):
        return None
    return total if math.isfinite(total) else None


# Проверяет, что у открытой линии есть тотал и оба коэффициента Over/Under.
def _has_valid_open_over_data(entry):
    return (
        _to_total(entry.get("ov", {}).get("over")) is not None
        and _to_float(entry.get("ov", {}).get("over_odds")) is not None
        and _to_float(entry.get("ov", {}).get("under_odds")) is not None
    )


# Проверяет, укладывается ли разница дат записей в 10 минут.
def _dates_within_ten_minutes(first_entry, second_entry):
    try:
        first_date = datetime.fromisoformat(
            str(first_entry["date"]).replace("Z", "+00:00")
        )
        second_date = datetime.fromisoformat(
            str(second_entry["date"]).replace("Z", "+00:00")
        )
    except (KeyError, TypeError, ValueError):
        return False

    if (first_date.utcoffset() is None) != (second_date.utcoffset() is None):
        return False

    return abs(first_date - second_date) <= timedelta(minutes=10)


# Разбирает timestamp записи для отсчёта ожидания подтверждения нового счёта.
def _entry_datetime(entry):
    try:
        return datetime.fromisoformat(
            str(entry["date"]).replace("Z", "+00:00")
        )
    except (KeyError, TypeError, ValueError):
        return None


# Проверяет, не относится ли запись к времени матча из списка исключений.
def _is_skipped_match_entry(entry):
    skip_values = {str(value).strip().upper() for value in SKIPMATCH}

    for field_name in ("time", "match_time"):
        value = entry.get(field_name)
        if value is None:
            continue

        normalized_value = "".join(str(value).upper().split())
        if normalized_value in skip_values:
            return True

        if (
            "ET" in skip_values
            and normalized_value.startswith("ET+")
            and normalized_value[3:].isdigit()
        ):
            return True

    return False


# Подменяет устаревший счёт, пока новая линия после гола не будет подтверждена.
def _normalize_score_transitions(entries):
    normalized_entries = []
    last_score = None
    last_numeric_entry = None
    waiting_for_new_total = False
    saw_over_closed = False
    pending_score = None
    transition_baseline = None
    score_change_datetime = None

    for entry in entries:
        normalized_entry = entry.copy()
        score = entry.get("score")
        has_score = score is not None and str(score).strip().lower() not in {
            "",
            "-",
            "unknown",
        }
        is_over_closed = entry.get("ov", {}).get("over") == "Closed"
        has_valid_open_data = _has_valid_open_over_data(entry)

        if has_score and last_score is None:
            last_score = score

        if waiting_for_new_total:
            if has_score and score != last_score:
                pending_score = score

            if is_over_closed:
                saw_over_closed = True

            entry_datetime = _entry_datetime(entry)
            timeout_elapsed = False
            if entry_datetime is not None and score_change_datetime is not None:
                if (entry_datetime.utcoffset() is None) == (
                    score_change_datetime.utcoffset() is None
                ):
                    timeout_elapsed = (
                        entry_datetime - score_change_datetime
                        >= timedelta(minutes=3)
                    )

            if timeout_elapsed and has_valid_open_data:
                last_score = score if has_score else pending_score or last_score
                last_numeric_entry = normalized_entry
                waiting_for_new_total = False
                saw_over_closed = False
                pending_score = None
                transition_baseline = None
                score_change_datetime = None
            elif is_over_closed or not has_valid_open_data or not saw_over_closed:
                normalized_entry["score"] = last_score
            elif transition_baseline is None:
                normalized_entry["score"] = pending_score or score or last_score
                last_score = normalized_entry["score"]
                last_numeric_entry = normalized_entry
                waiting_for_new_total = False
                saw_over_closed = False
                pending_score = None
                score_change_datetime = None
            else:
                baseline_total = _to_total(
                    transition_baseline.get("ov", {}).get("over")
                )
                current_total = _to_total(entry.get("ov", {}).get("over"))
                if current_total == baseline_total:
                    normalized_entry["score"] = last_score
                else:
                    normalized_entry["score"] = pending_score or score or last_score
                    last_score = normalized_entry["score"]
                    last_numeric_entry = normalized_entry
                    waiting_for_new_total = False
                    saw_over_closed = False
                    pending_score = None
                    transition_baseline = None
                    score_change_datetime = None
        elif has_score and score != last_score:
            pending_score = score
            transition_baseline = last_numeric_entry
            waiting_for_new_total = True
            saw_over_closed = is_over_closed
            score_change_datetime = _entry_datetime(entry)
            normalized_entry["score"] = last_score

            if not is_over_closed and has_valid_open_data and saw_over_closed:
                if transition_baseline is None:
                    normalized_entry["score"] = score
                    last_score = score
                    last_numeric_entry = normalized_entry
                    waiting_for_new_total = False
                    saw_over_closed = False
                    pending_score = None
                    score_change_datetime = None
                else:
                    baseline_total = _to_total(
                        transition_baseline.get("ov", {}).get("over")
                    )
                    current_total = _to_total(entry.get("ov", {}).get("over")                    )
                    if current_total != baseline_total:
                        normalized_entry["score"] = score
                        last_score = score
                        last_numeric_entry = normalized_entry
                        waiting_for_new_total = False
                        saw_over_closed = False
                        pending_score = None
                        transition_baseline = None
                        score_change_datetime = None
        else:
            if has_score:
                last_score = score

            if not is_over_closed and has_valid_open_data:
                normalized_entry["score"] = last_score or score
                last_numeric_entry = normalized_entry

        normalized_entries.append(normalized_entry)

    return normalized_entries


# Возвращает историю со счётом, скорректированным для анализа стратегии.
def _relabel_stale_score_entries(entries):
    return _normalize_score_transitions(entries)


# Возвращает историю для Over-стратегии с корректировкой счёта и сохраняет её для отладки.
def _prepare_over_total_drop_history(match_history):
    prepared_history = {}

    for match_id, match_data in match_history.items():
        prepared_match = match_data.copy()
        has_initial = bool(match_data.get("initial"))
        entries = (
            [match_data["initial"]] if has_initial else []
        ) + list(match_data.get("changes", []))
        normalized_entries = _normalize_score_transitions(entries)

        if has_initial:
            prepared_match["initial"] = normalized_entries[0]
            prepared_match["changes"] = normalized_entries[1:]
        else:
            prepared_match["changes"] = normalized_entries
        prepared_history[match_id] = prepared_match
        prepared_history[match_id] = prepared_match

    if DEBUGMODE == 1:
        snapshot_path = os.path.join(os.path.dirname(__file__), "data2.json")
        try:
            with open(snapshot_path, "w", encoding="utf-8") as snapshot_file:
                json.dump(
                    prepared_history,
                    snapshot_file,
                    ensure_ascii=False,
                    indent=2,
                )
                snapshot_file.write("\n")
        except OSError:
            logger.exception(
                "Failed to write Over strategy input snapshot to %s",
                snapshot_path,
            )

    return prepared_history


# Создаёт callback для регистрации отправленного Over-сигнала.
def _notification_sent_callback(on_notification_sent, match_id, league):
    if not on_notification_sent:
        return None

    # Передаёт callback-у метаданные отправленного уведомления.
    def register(message_id, message, link, prediction):
        on_notification_sent({
            "match_id": match_id,
            "market": "ov",
            "league": league,
            "message_id": message_id,
            "message": message,
            "link": link,
            "prediction": prediction,
        })

    return register


# Отправляет уведомление о найденном движении тотала.
def _send_over_notification(match_id, entry, total, odds, on_notification_sent=None):
    league = entry.get("league", "Unknown")

    try:
        send_telegram_notification(
            league=league,
            team1=entry.get("team1", "Unknown"),
            team2=entry.get("team2", "Unknown"),
            score=entry.get("score", "Unknown"),
            over=total,
            over_odds=odds,
            match_id=match_id,
            match_time=entry.get("match_time", "Unknown"),
            on_sent_details=_notification_sent_callback(
                on_notification_sent, match_id, league
            ),
        )
    except Exception as error:
        logger.error(f"Match {match_id}: Failed to send notification: {error}")


# Ищет рост тотала в пределах одного счёта и временного окна.
def _find_over_total_drop_pattern(
    entries,
    match_id,
    on_notification_sent=None,
    *,
    scores_relabelled=False,
):
    if not scores_relabelled:
        entries = _normalize_score_transitions(entries)

    anchor_entry = None
    anchor_idx = -1
    anchor_total = None
    for idx in range(len(entries) - 1, -1, -1):
        current_entry = entries[idx]
        current_total = _to_total(current_entry.get("ov", {}).get("over"))
        if current_total is not None and _has_valid_open_over_data(current_entry):
            anchor_entry = entries[idx]
            anchor_idx = idx
            anchor_total = current_total
            break

    if anchor_entry is None or anchor_total is None:
        return False

    anchor_score = anchor_entry.get("score")
    if anchor_score is None or str(anchor_score).strip().lower() in {"", "-", "unknown"}:
        return False
    if _is_skipped_match_entry(anchor_entry):
        return False

    for idx in range(anchor_idx - 1, -1, -1):
        current_entry = entries[idx]
        if current_entry.get("score") != anchor_score:
            break

        current_total = _to_total(current_entry.get("ov", {}).get("over"))
        if current_total is None or not _has_valid_open_over_data(current_entry):
            continue
        if not _dates_within_ten_minutes(anchor_entry, current_entry):
            continue
        if _is_skipped_match_entry(current_entry):
            continue

        total_increase = anchor_total - current_total
        if OVER_TOTAL_DROP_THRESHOLD <= total_increase < OVER_TOTAL_DROP_MAX:
            _send_over_notification(
                match_id,
                anchor_entry,
                anchor_entry.get("ov", {}).get("over"),
                _to_float(anchor_entry.get("ov", {}).get("over_odds")),
                on_notification_sent,
            )
            return True

    return False
