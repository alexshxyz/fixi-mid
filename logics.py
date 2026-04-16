import logging
from telegram_notifier import send_telegram_notification

# Настройка логирования
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Handler для файла
import os
log_file = os.path.join(os.path.dirname(__file__), 'bot.log')
file_handler = logging.FileHandler(log_file)
file_handler.setLevel(logging.INFO)
file_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
file_handler.setFormatter(file_formatter)

# Handler для консоли
console_handler = logging.StreamHandler()
console_handler.setLevel(logging.INFO)
console_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
console_handler.setFormatter(console_formatter)

logger.addHandler(file_handler)
logger.addHandler(console_handler)

THRESHOLD = 0.61  # Изменяй это значение для настройки порога


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
    return bool(__import__("re").match(r"^\+?0\s*/\s*-[\d.,]+$", text))


def _ah_sign(value):
    text = _normalize_ah_text(value)
    if text is None:
        return None
    if _is_exact_zero_ah(text):
        return 0
    if _is_away_zero_split_handicap(text):
        return -1
    return -1 if text.startswith("-") else 1


def _invert_handicap(value):
    text = _normalize_ah_text(value)
    if text is None:
        return None
    if text.startswith("-"):
        return text[1:]
    if text.startswith("+"):
        return f"-{text[1:]}"
    return f"-{text}"


def _ov_closed_pattern(prev_entry, current_entry):
    if current_entry.get("ov", {}).get("over") != "Closed":
        return False

    over_odds = _to_float(prev_entry.get("ov", {}).get("over_odds"))
    return over_odds is not None and over_odds <= THRESHOLD


def _ah_closed_pattern(prev_entry, current_entry):
    if current_entry.get("ah", {}).get("ah") != "Closed":
        return False

    raw_ah = prev_entry.get("ah", {}).get("ah")
    sign = _ah_sign(raw_ah)
    if sign is None or sign == 0:
        return False

    if sign > 0 and not _is_away_zero_split_handicap(raw_ah):
        odds = _to_float(prev_entry.get("ah", {}).get("home_ah_odds"))
    else:
        odds = _to_float(prev_entry.get("ah", {}).get("away_ah_odds"))

    return odds is not None and odds <= THRESHOLD


def _to_float(value):
    try:
        return float(value)
    except (ValueError, TypeError):
        return None


def _ov_closed_pattern(prev_entry, current_entry):
    if current_entry.get("ov", {}).get("over") != "Closed":
        return False

    over_odds = _to_float(prev_entry.get("ov", {}).get("over_odds"))
    return over_odds is not None and over_odds <= THRESHOLD


def find_pattern_matches(match_history):
    sent_matches = []

    for match_id, data in match_history.items():
        entries = []
        if data.get("initial"):
            entries.append(data["initial"])
        entries.extend(data.get("changes", []))

        for idx in range(1, len(entries)):
            prev_entry = entries[idx - 1]
            current_entry = entries[idx]

            ov_match = _ov_closed_pattern(prev_entry, current_entry)
            ah_match = _ah_closed_pattern(prev_entry, current_entry)

            team1 = prev_entry.get("team1", "Unknown")
            team2 = prev_entry.get("team2", "Unknown")
            score = prev_entry.get("score", "Unknown")
            league = prev_entry.get("league", "Unknown")

            if ov_match:
                over = prev_entry.get("ov", {}).get("over", "Unknown")
                over_odds = prev_entry.get("ov", {}).get("over_odds", "Unknown")
                send_telegram_notification(
                    league=league,
                    team1=team1,
                    team2=team2,
                    score=score,
                    over=over,
                    over_odds=over_odds,
                    match_id=match_id
                )
                sent_matches.append(match_id)

            if ah_match:
                raw_ah = prev_entry.get("ah", {}).get("ah")
                sign = _ah_sign(raw_ah)
                if sign is not None and sign != 0:
                    if sign > 0 and not _is_away_zero_split_handicap(raw_ah):
                        handicap = _invert_handicap(raw_ah)
                        handicap_order = "1"
                        odds = prev_entry.get("ah", {}).get("home_ah_odds", "Unknown")
                    else:
                        handicap = raw_ah
                        handicap_order = "2"
                        odds = prev_entry.get("ah", {}).get("away_ah_odds", "Unknown")

                    send_telegram_notification(
                        league=league,
                        team1=team1,
                        team2=team2,
                        score=score,
                        match_id=match_id,
                        over_odds=odds,
                        handicap_text=handicap,
                        handicap_team_order=handicap_order
                    )
                    sent_matches.append(match_id)

            if ov_match or ah_match:
                break

    return sent_matches


if __name__ == "__main__":
    # Для тестирования, но в реальности match_history передается
    from parser import match_history
    lines = find_pattern_matches(match_history)
    if lines:
        logger.info(f"Pattern found: {lines}")
    else:
        logger.info("No matches found")
