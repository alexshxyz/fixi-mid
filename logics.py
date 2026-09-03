import json
import re

from telegram_notifier import send_telegram_notification
from logging_config import setup_logger

logger = setup_logger(__name__)

THRESHOLD = 0.61  # Изменяй это значение для настройки порога. Идеально - 0.61
MAX_ODD = 0.80   # Максимальный коэффициент на ТБ для срабатывания паттерна. Идеально - 0.80


def _to_float(value):
    try:
        return float(value)
    except (ValueError, TypeError):
        return None


def _normalize_ah_text(value):
    if value is None:
        return None
    text = str(value).strip()
    return text if text else None


def _is_exact_zero_ah(value):
    text = _normalize_ah_text(value)
    return text in {"0", "0.0", "+0", "-0", "-0.0"}


def _is_away_zero_split_handicap(value):
    text = _normalize_ah_text(value)
    if text is None:
        return False
    # Example: 0/-0.5 — это фора второй команды
    return bool(re.match(r"^\+?0\s*/\s*-[\d.,]+$", text))


def _ah_sign(value):
    text = _normalize_ah_text(value)
    if text is None:
        return None
    if _is_exact_zero_ah(text):
        return 0
    if _is_away_zero_split_handicap(text):
        return -1
    return -1 if text.startswith("-") else 1


def _collect_match_entries(data):
    """Собирает историю матча в один список для дальнейшего анализа."""
    entries = []
    if data.get("initial"):
        entries.append(data["initial"])
    entries.extend(data.get("changes", []))
    return entries


def _get_last_entry_before_closed(entries, field_name):
    """Находит последнюю запись до состояния Closed по конкретному полю: ov или ah."""
    for idx in range(len(entries) - 2, -1, -1):
        if entries[idx].get(field_name, {}).get(field_name) != "Closed":
            return entries[idx], idx
    return None, -1


def _send_over_notification(match_id, last_entry, last_total, last_over_odds):
    """Отправляет Telegram-уведомление для найденного over-паттерна."""
    team1 = last_entry.get("team1", "Unknown")
    team2 = last_entry.get("team2", "Unknown")
    score = last_entry.get("score", "Unknown")
    league = last_entry.get("league", "Unknown")

    try:
        send_telegram_notification(
            league=league,
            team1=team1,
            team2=team2,
            score=score,
            over=last_total,
            over_odds=last_over_odds,
            match_id=match_id
        )
    except Exception as e:
        logger.error(f"Match {match_id}: Failed to send notification: {e}")


def _find_over_pattern(entries, match_id):
    """Проверяет, есть ли для total over паттерн с закрытием линии и высоким коэффициентом."""
    if len(entries) < 2 or entries[-1].get("ov", {}).get("over") != "Closed":
        logger.debug(f"Match {match_id}: No 'Closed' in last entry or insufficient entries")
        return False

    last_entry, last_idx = _get_last_entry_before_closed(entries, "ov")
    if last_entry is None:
        return False

    last_over_odds = _to_float(last_entry.get("ov", {}).get("over_odds"))
    last_total = last_entry.get("ov", {}).get("over")

    if last_over_odds is None or last_total is None or last_total == "Closed" or last_over_odds > THRESHOLD:
        logger.debug(f"Match {match_id}: Base condition not met")
        return False

    start_search_idx = last_idx - 1
    for search_idx in range(start_search_idx, -1, -1):
        current_entry = entries[search_idx]

        if current_entry.get("ov", {}).get("over") == "Closed":
            continue

        current_total = current_entry.get("ov", {}).get("over")
        current_over_odds = _to_float(current_entry.get("ov", {}).get("over_odds"))

        if current_total != last_total:
            break

        if current_over_odds is not None and current_over_odds >= MAX_ODD:
            _send_over_notification(match_id, last_entry, last_total, last_over_odds)
            return True

    logger.debug(f"Match {match_id}: No matching entry found above")
    return False


def _send_ah_notification(match_id, last_entry, last_ah, last_ah_odds, odds_side):
    """Отправляет Telegram-уведомление для найденного handicaps-паттерна."""
    team1 = last_entry.get("team1", "Unknown")
    team2 = last_entry.get("team2", "Unknown")
    score = last_entry.get("score", "Unknown")
    league = last_entry.get("league", "Unknown")
    handicap = last_ah
    handicap_order = "Home" if odds_side == "home" else "Away"

    if handicap and not handicap.startswith("-") and not _is_away_zero_split_handicap(handicap):
        handicap = f"-{handicap}"

    try:
        send_telegram_notification(
            league=league,
            team1=team1,
            team2=team2,
            score=score,
            match_id=match_id,
            over_odds=last_ah_odds,
            handicap_text=handicap,
            handicap_team_order=handicap_order
        )
    except Exception as e:
        logger.error(f"Match {match_id}: Failed to send notification: {e}")


def _find_ah_pattern(entries, match_id):
    """Проверяет, есть ли для форы AH паттерн с закрытием и подтверждающим коэффициентом."""
    if len(entries) < 2 or entries[-1].get("ah", {}).get("ah") != "Closed":
        return False

    last_entry, last_idx = _get_last_entry_before_closed(entries, "ah")
    if last_entry is None:
        return False

    last_ah = last_entry.get("ah", {}).get("ah")
    raw_ah = last_ah
    sign = _ah_sign(raw_ah)
    last_ah_odds = None
    odds_side = None

    if sign is not None and sign != 0:
        if sign > 0 and not _is_away_zero_split_handicap(raw_ah):
            last_ah_odds = _to_float(last_entry.get("ah", {}).get("home_ah_odds"))
            odds_side = "home"
        else:
            last_ah_odds = _to_float(last_entry.get("ah", {}).get("away_ah_odds"))
            odds_side = "away"

    if not last_ah or last_ah == "Closed" or last_ah_odds is None or last_ah_odds > THRESHOLD:
        return False

    start_search_idx = last_idx - 1
    for search_idx in range(start_search_idx, -1, -1):
        current_entry = entries[search_idx]
        if current_entry.get("ah", {}).get("ah") == "Closed":
            continue

        current_ah = current_entry.get("ah", {}).get("ah")
        if current_ah != last_ah:
            break

        if odds_side == "home":
            current_ah_odds = _to_float(current_entry.get("ah", {}).get("home_ah_odds"))
        else:
            current_ah_odds = _to_float(current_entry.get("ah", {}).get("away_ah_odds"))

        if current_ah_odds is not None and current_ah_odds >= MAX_ODD:
            _send_ah_notification(match_id, last_entry, last_ah, last_ah_odds, odds_side)
            return True

    return False


def find_pattern_matches(match_history):
    """Главная функция: проходит по всем матчам и возвращает ID тех, где сработал паттерн."""
    sent_matches = []

    for match_id, data in match_history.items():
        entries = _collect_match_entries(data)

        if _find_over_pattern(entries, match_id):
            sent_matches.append(match_id)

        if _find_ah_pattern(entries, match_id):
            sent_matches.append(match_id)

    return sent_matches


if __name__ == "__main__":
    # Для тестирования, но в реальности match_history передается
    try:
        with open('matches_realtime.json', 'r', encoding='utf-8') as f:
            data = json.load(f)
        match_history = {}
        for match_id, match_data in data.items():
            match_history[match_id] = {
                'initial': match_data['initial'],
                'changes': match_data['changes']
            }
        logger.info(f"Loaded {len(match_history)} matches from matches_realtime.json")
    except Exception as e:
        logger.error(f"Failed to load matches_realtime.json: {e}")
        match_history = {}
    lines = find_pattern_matches(match_history)
    if lines:
        logger.info(f"Pattern found: {lines}")
    else:
        logger.info("No matches found")
