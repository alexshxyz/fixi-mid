import re

from .config import FINISHED_TIME_MARKERS, START_ODD, THRESHOLD
from .notifier import send_telegram_notification
from .logger import setup_logger

logger = setup_logger(__name__)


# Преобразует значение в число с плавающей точкой или возвращает None.
def _to_float(value):
    try:
        return float(value)
    except (ValueError, TypeError):
        return None


# Приводит значение форы к очищенной строке или возвращает None.
def _normalize_ah_text(value):
    if value is None:
        return None
    text = str(value).strip()
    return text if text else None


# Проверяет, является ли значение форы одним из вариантов нуля.
def _is_exact_zero_ah(value):
    text = _normalize_ah_text(value)
    return text in {"0", "0.0", "+0", "-0", "-0.0"}


# Распознаёт азиатскую фору вида 0/-0.5 для второй команды.
def _is_away_zero_split_handicap(value):
    text = _normalize_ah_text(value)
    if text is None:
        return False
    # Example: 0/-0.5 — это фора второй команды
    return bool(re.match(r"^\+?0\s*/\s*-[\d.,]+$", text))


# Определяет знак форы: 1 для первой команды, -1 для второй, 0 для нуля.
def _ah_sign(value):
    text = _normalize_ah_text(value)
    if text is None:
        return None
    if _is_exact_zero_ah(text):
        return 0
    if _is_away_zero_split_handicap(text):
        return -1
    return -1 if text.startswith("-") else 1


# Собирает историю матча в один список для дальнейшего анализа.
def _collect_match_entries(data):
    entries = []
    if data.get("initial"):
        entries.append(data["initial"])
    entries.extend(data.get("changes", []))
    return entries


# Находит последнюю запись до состояния Closed по конкретному полю: ov или ah.
def _get_last_entry_before_closed(entries, field_name):
    for idx in range(len(entries) - 2, -1, -1):
        line_field = "over" if field_name == "ov" else field_name
        if entries[idx].get(field_name, {}).get(line_field) != "Closed":
            return entries[idx], idx
    return None, -1


# Сравнивает счета; неизвестный счет не блокирует отправку сигнала.
def _scores_match(closed_entry, confirming_entry):
    # Возвращает счёт, если он задан и известен.
    def known_score(entry):
        score = entry.get("score")
        if score is None:
            return None
        score = str(score).strip()
        return None if not score or score.casefold() == "unknown" else score

    closed_score = known_score(closed_entry)
    confirming_score = known_score(confirming_entry)
    return (
        closed_score is None
        or confirming_score is None
        or closed_score == confirming_score
    )


# Проверяет, указывает ли игровое время на завершение матча.
def _is_finished_match(entry):
    for field in ("time", "match_time"):
        value = entry.get(field)
        if value is None:
            continue
        time_text = str(value).casefold()
        if any(marker.casefold() in time_text for marker in FINISHED_TIME_MARKERS):
            return True
    return False


# Подготавливает callback с данными успешно отправленного сигнала.
def _notification_sent_callback(on_notification_sent, match_id, market, league):
    if not on_notification_sent:
        return None

    # Передаёт callback-у данные об отправленном сообщении.
    def register(message_id, message, link, prediction, channel_id):
        on_notification_sent({
            'match_id': match_id,
            'market': market,
            'league': league,
            'message_id': message_id,
            'message': message,
            'link': link,
            'prediction': prediction,
            'channel_id': channel_id,
        })

    return register


# Отправляет Telegram-уведомление для найденного паттерна рынка.
def _send_market_notification(
    market,
    match_id,
    last_entry,
    line,
    odds,
    drop_type,
    odds_side=None,
    on_notification_sent=None,
):
    team1 = last_entry.get("team1", "Unknown")
    team2 = last_entry.get("team2", "Unknown")
    score = last_entry.get("score", "Unknown")
    league = last_entry.get("league", "Unknown")
    match_time = last_entry.get("match_time", "Unknown")

    notification = {
        "league": league,
        "team1": team1,
        "team2": team2,
        "score": score,
        "drop_type": drop_type,
        "match_id": match_id,
        "match_time": match_time,
        "on_sent_details": _notification_sent_callback(
            on_notification_sent, match_id, market, league
        ),
        "strategy": "default",
    }
    if market == "ov":
        notification.update(over=line, over_odds=odds)
    else:
        handicap = line
        if handicap and not handicap.startswith("-") and _ah_sign(handicap) != -1:
            handicap = f"-{handicap}"
        notification.update(
            over_odds=odds,
            handicap_text=handicap,
            handicap_team_order="Home" if odds_side == "home" else "Away",
        )

    try:
        send_telegram_notification(**notification)
    except Exception as e:
        logger.error(f"Match {match_id}: Failed to send notification: {e}")


# Отправляет Telegram-уведомление для найденного over-паттерна.
def _send_over_notification(
    match_id,
    last_entry,
    last_total,
    last_over_odds,
    drop_type,
    on_notification_sent=None,
):
    _send_market_notification(
        "ov",
        match_id,
        last_entry,
        last_total,
        last_over_odds,
        drop_type,
        on_notification_sent=on_notification_sent,
    )


# Отправляет Telegram-уведомление для найденного handicaps-паттерна.
def _send_ah_notification(
    match_id,
    last_entry,
    last_ah,
    last_ah_odds,
    odds_side,
    drop_type,
    on_notification_sent=None,
):
    _send_market_notification(
        "ah",
        match_id,
        last_entry,
        last_ah,
        last_ah_odds,
        drop_type,
        odds_side=odds_side,
        on_notification_sent=on_notification_sent,
    )


def _get_market_values(entry, market):
    market_data = entry.get(market, {})
    if market == "ov":
        return (
            market_data.get("over"),
            _to_float(market_data.get("over_odds")),
            None,
        )

    handicap = market_data.get("ah")
    sign = _ah_sign(handicap)
    if sign in (None, 0):
        return handicap, None, None

    odds_side = "home" if sign > 0 else "away"
    odds_key = "home_ah_odds" if odds_side == "home" else "away_ah_odds"
    return handicap, _to_float(market_data.get(odds_key)), odds_side


# Проверяет общий паттерн закрытия линии с последующим подтверждающим коэффициентом.
def _find_market_pattern(entries, match_id, market, notify, on_notification_sent):
    line_key = "over" if market == "ov" else "ah"
    if (
        len(entries) < 2
        or entries[-1].get(market, {}).get(line_key) != "Closed"
        or _is_finished_match(entries[-1])
    ):
        return False

    last_entry, last_idx = _get_last_entry_before_closed(entries, market)
    if last_entry is None:
        return False

    last_line, last_odds, odds_side = _get_market_values(last_entry, market)

    if last_line is None or last_odds is None or last_odds > THRESHOLD:
        return False

    start_search_idx = last_idx - 1
    for search_idx in range(start_search_idx, -1, -1):
        current_entry = entries[search_idx]

        if current_entry.get(market, {}).get(line_key) == "Closed":
            continue

        current_line, current_odds, _ = _get_market_values(current_entry, market)

        if current_line != last_line:
            break

        if current_odds is not None and current_odds >= START_ODD:
            if not _scores_match(entries[-1], current_entry):
                return False
            drop_type = f"ODDS {current_odds:.2f} -> {last_odds:.2f}"
            if market == "ov":
                notify(
                    match_id,
                    last_entry,
                    last_line,
                    last_odds,
                    drop_type,
                    on_notification_sent,
                )
            else:
                notify(
                    match_id,
                    last_entry,
                    last_line,
                    last_odds,
                    odds_side,
                    drop_type,
                    on_notification_sent,
                )
            return True

    return False


# Проверяет, есть ли для total over паттерн с закрытием линии и высоким коэффициентом.
def _find_over_pattern(entries, match_id, on_notification_sent=None):
    return _find_market_pattern(
        entries, match_id, "ov", _send_over_notification, on_notification_sent
    )


# Проверяет, есть ли для форы AH паттерн с закрытием и подтверждающим коэффициентом.
def _find_ah_pattern(entries, match_id, on_notification_sent=None):
    return _find_market_pattern(
        entries, match_id, "ah", _send_ah_notification, on_notification_sent
    )


# Главная функция: проходит по всем матчам и возвращает ID тех, где сработал паттерн.
def find_pattern_matches(
    match_history,
    on_notification_sent=None,
):
    sent_matches = []

    for match_id, data in match_history.items():
        entries = _collect_match_entries(data)

        if _find_over_pattern(entries, match_id, on_notification_sent):
            sent_matches.append(match_id)

        if _find_ah_pattern(entries, match_id, on_notification_sent):
            sent_matches.append(match_id)

    return sent_matches
