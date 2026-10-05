import math
from datetime import datetime, timedelta

from config import OVER_TOTAL_DROP_MAX, OVER_TOTAL_DROP_THRESHOLD
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


# Подменяет устаревший счёт до закрытия линии, не изменяя исходные записи.
def _relabel_stale_score_entries(entries):
    normalized_entries = []
    last_score = None
    waiting_for_over_reopen = False

    for entry in entries:
        normalized_entry = entry.copy()
        score = entry.get("score")
        has_score = score is not None and str(score).strip().lower() not in {
            "",
            "-",
            "unknown",
        }
        is_over_closed = entry.get("ov", {}).get("over") == "Closed"

        if waiting_for_over_reopen:
            if is_over_closed:
                waiting_for_over_reopen = False
                if has_score:
                    last_score = score
            else:
                normalized_entry["score"] = last_score
        elif has_score:
            if last_score is not None and score != last_score and not is_over_closed:
                normalized_entry["score"] = last_score
                waiting_for_over_reopen = True
            else:
                last_score = score

        normalized_entries.append(normalized_entry)

    return normalized_entries


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
def _find_over_total_drop_pattern(entries, match_id, on_notification_sent=None):
    entries = _relabel_stale_score_entries(entries)

    anchor_entry = None
    anchor_idx = -1
    anchor_total = None
    for idx in range(len(entries) - 1, -1, -1):
        current_total = _to_total(entries[idx].get("ov", {}).get("over"))
        if current_total is not None:
            anchor_entry = entries[idx]
            anchor_idx = idx
            anchor_total = current_total
            break

    if anchor_entry is None or anchor_total is None:
        return False

    anchor_score = anchor_entry.get("score")
    if anchor_score is None or str(anchor_score).strip().lower() in {"", "-", "unknown"}:
        return False
    baseline_total = None
    for idx in range(anchor_idx - 1, -1, -1):
        current_entry = entries[idx]
        if current_entry.get("score") != anchor_score:
            break

        current_total = _to_total(current_entry.get("ov", {}).get("over"))
        if (
            current_total is not None
            and _dates_within_ten_minutes(anchor_entry, current_entry)
        ):
            baseline_total = current_total

    if baseline_total is None:
        return False

    total_increase = anchor_total - baseline_total
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
