import math

from config import OVER_TOTAL_DROP_MAX, OVER_TOTAL_DROP_THRESHOLD
from logger import setup_logger
from notifier import send_telegram_notification

logger = setup_logger(__name__)


def _to_float(value):
    try:
        return float(value)
    except (ValueError, TypeError):
        return None


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


def _notification_sent_callback(on_notification_sent, match_id, league):
    if not on_notification_sent:
        return None

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


def _find_over_total_drop_pattern(entries, match_id, on_notification_sent=None):
    last_known_score = None
    score_change_idx = None

    for idx, entry in enumerate(entries):
        score = entry.get("score")
        if score is None or str(score).strip().lower() in {"", "-", "unknown"}:
            continue

        if last_known_score is not None and score != last_known_score:
            score_change_idx = idx
        last_known_score = score

    post_score_change = score_change_idx is not None
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
    found_post_score_close = False
    for idx in range(anchor_idx - 1, -1, -1):
        current_entry = entries[idx]
        if current_entry.get("score") != anchor_score:
            break

        if post_score_change and idx < score_change_idx:
            break

        if current_entry.get("ov", {}).get("over") == "Closed":
            if post_score_change and idx >= score_change_idx:
                found_post_score_close = True
            break

        current_total = _to_total(current_entry.get("ov", {}).get("over"))
        if current_total is not None:
            baseline_total = current_total

    if post_score_change and not found_post_score_close:
        return False
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
