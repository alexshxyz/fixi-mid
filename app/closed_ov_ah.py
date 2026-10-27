import re
from collections.abc import Callable
from threading import Event

from .config import FINISHED_TIME_MARKERS, START_ODD, THRESHOLD
from .notifier import send_telegram_notification
from .logger import setup_logger
from .match_types import (
    HandicapMarketLine,
    MarketType,
    MarketValue,
    MatchHistory,
    MatchHistoryMap,
    MatchSnapshot,
    NotificationSentCallback,
    NotificationSentRegistrar,
    OddsSide,
    OverMarketLine,
    TelegramNotification,
)

logger = setup_logger(__name__)


def _to_float(value: MarketValue) -> float | None:
    """Преобразовать значение рынка в число или вернуть None."""
    if value is None:
        return None
    try:
        return float(value)
    except (ValueError, TypeError):
        return None


def _normalize_ah_text(value: MarketValue) -> str | None:
    """Очистить значение форы и вернуть None для пустой строки."""
    if value is None:
        return None
    text = str(value).strip()
    return text if text else None


def _is_exact_zero_ah(value: MarketValue) -> bool:
    """Проверить, обозначает ли значение нулевую фору."""
    text = _normalize_ah_text(value)
    return text in {"0", "0.0", "+0", "-0", "-0.0"}


def _is_away_zero_split_handicap(value: MarketValue) -> bool:
    """Распознать дробную фору ноль/-значение для второй команды."""
    text = _normalize_ah_text(value)
    if text is None:
        return False
    return bool(re.match(r"^\+?0\s*/\s*-[\d.,]+$", text))


def _ah_sign(value: MarketValue) -> int | None:
    """Определить сторону форы: 1, -1, 0 или None."""
    text = _normalize_ah_text(value)
    if text is None:
        return None
    if _is_exact_zero_ah(text):
        return 0
    if _is_away_zero_split_handicap(text):
        return -1
    return -1 if text.startswith("-") else 1


def _collect_match_entries(data: MatchHistory) -> list[MatchSnapshot]:
    """Собрать исходный снимок и изменения в хронологическом порядке."""
    entries: list[MatchSnapshot] = []
    initial = data.get("initial")
    if initial:
        entries.append(initial)
    entries.extend(data.get("changes", []))
    return entries


def _line_status(entry: MatchSnapshot, market: MarketType) -> str | None:
    """Получить значение линии рынка из снимка матча."""
    if market == "ov":
        return entry.get("ov", {}).get("over")
    return entry.get("ah", {}).get("ah")


def _get_last_entry_before_closed(
    entries: list[MatchSnapshot],
    market: MarketType,
) -> tuple[MatchSnapshot | None, int]:
    """Найти последний снимок до закрытия линии указанного рынка."""
    for idx in range(len(entries) - 2, -1, -1):
        if _line_status(entries[idx], market) != "Closed":
            return entries[idx], idx
    return None, -1


def _scores_match(
    closed_entry: MatchSnapshot,
    confirming_entry: MatchSnapshot,
) -> bool:
    """Сравнить известные счета; неизвестный счет не блокирует сигнал."""
    def known_score(entry: MatchSnapshot) -> str | None:
        """Вернуть известный счёт или None."""
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


def _is_finished_match(entry: MatchSnapshot) -> bool:
    """Проверить, указывает ли снимок на завершение матча."""
    for field in ("time", "match_time"):
        value = entry.get(field)
        if value is None:
            continue
        time_text = str(value).casefold()
        if any(marker.casefold() in time_text for marker in FINISHED_TIME_MARKERS):
            return True
    return False


def _notification_sent_callback(
    on_notification_sent: NotificationSentCallback | None,
    match_id: str,
    market: MarketType,
    league: str,
) -> NotificationSentRegistrar | None:
    """Связать callback уведомления с данными матча и рынка."""
    if not on_notification_sent:
        return None

    def register(
        message_id: str | int,
        message: str,
        link: str,
        prediction: str,
        channel_id: str | None,
    ) -> None:
        """Передать callback-у сведения об успешно отправленном уведомлении."""
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


def _send_market_notification(
    market: MarketType,
    match_id: str,
    last_entry: MatchSnapshot,
    line: str | None,
    odds: float,
    drop_type: str,
    odds_side: OddsSide | None = None,
    on_notification_sent: NotificationSentCallback | None = None,
    shutdown_requested: Event | None = None,
) -> None:
    """Отправить Telegram-уведомление о найденном паттерне рынка."""
    if shutdown_requested is not None and shutdown_requested.is_set():
        return
    team1 = last_entry.get("team1", "Unknown")
    team2 = last_entry.get("team2", "Unknown")
    score = last_entry.get("score", "Unknown")
    league = last_entry.get("league", "Unknown")
    match_time = last_entry.get("match_time", "Unknown")

    notification: TelegramNotification = {
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
        notification["over"] = line
        notification["over_odds"] = odds
    else:
        handicap = line
        if handicap and not handicap.startswith("-") and _ah_sign(handicap) != -1:
            handicap = f"-{handicap}"
        notification["over_odds"] = odds
        notification["handicap_text"] = handicap
        notification["handicap_team_order"] = (
            "Home" if odds_side == "home" else "Away"
        )

    try:
        if shutdown_requested is None:
            send_telegram_notification(**notification)
        else:
            send_telegram_notification(
                **notification, shutdown_requested=shutdown_requested
            )
    except Exception as e:
        logger.error(f"Match {match_id}: Failed to send notification: {e}")


def _send_over_notification(
    match_id: str,
    last_entry: MatchSnapshot,
    last_total: str,
    last_over_odds: float,
    drop_type: str,
    on_notification_sent: NotificationSentCallback | None = None,
    shutdown_requested: Event | None = None,
) -> None:
    """Отправить уведомление о паттерне Over."""
    _send_market_notification(
        "ov",
        match_id,
        last_entry,
        last_total,
        last_over_odds,
        drop_type,
        on_notification_sent=on_notification_sent,
        shutdown_requested=shutdown_requested,
    )


def _send_ah_notification(
    match_id: str,
    last_entry: MatchSnapshot,
    last_ah: str,
    last_ah_odds: float,
    odds_side: OddsSide,
    drop_type: str,
    on_notification_sent: NotificationSentCallback | None = None,
    shutdown_requested: Event | None = None,
) -> None:
    """Отправить уведомление о паттерне азиатской форы."""
    _send_market_notification(
        "ah",
        match_id,
        last_entry,
        last_ah,
        last_ah_odds,
        drop_type,
        odds_side=odds_side,
        on_notification_sent=on_notification_sent,
        shutdown_requested=shutdown_requested,
    )


def _get_market_values(
    entry: MatchSnapshot,
    market: MarketType,
) -> tuple[str | None, float | None, OddsSide | None]:
    """Получить линию, коэффициент и при необходимости сторону команды."""
    if market == "ov":
        over_data: OverMarketLine = entry.get("ov", {})
        return (
            over_data.get("over"),
            _to_float(over_data.get("over_odds")),
            None,
        )

    handicap_data: HandicapMarketLine = entry.get("ah", {})
    handicap = handicap_data.get("ah")
    sign = _ah_sign(handicap)
    if sign in (None, 0):
        return handicap, None, None

    odds_side: OddsSide = "home" if sign > 0 else "away"
    odds = (
        handicap_data.get("home_ah_odds")
        if odds_side == "home"
        else handicap_data.get("away_ah_odds")
    )
    return handicap, _to_float(odds), odds_side


def _find_market_pattern(
    entries: list[MatchSnapshot],
    match_id: str,
    market: MarketType,
    notify: Callable[..., None],
    on_notification_sent: NotificationSentCallback | None,
    shutdown_requested: Event | None = None,
) -> bool:
    """Проверить закрытие линии после подходящего изменения коэффициента."""
    if shutdown_requested is not None and shutdown_requested.is_set():
        return False
    if (
        len(entries) < 2
        or _line_status(entries[-1], market) != "Closed"
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
        if shutdown_requested is not None and shutdown_requested.is_set():
            return False
        current_entry = entries[search_idx]

        if _line_status(current_entry, market) == "Closed":
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
                    shutdown_requested,
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
                    shutdown_requested,
                )
            return True

    return False


def _find_over_pattern(
    entries: list[MatchSnapshot],
    match_id: str,
    on_notification_sent: NotificationSentCallback | None = None,
    shutdown_requested: Event | None = None,
) -> bool:
    """Проверить паттерн закрытия линии Over."""
    return _find_market_pattern(
        entries,
        match_id,
        "ov",
        _send_over_notification,
        on_notification_sent,
        shutdown_requested,
    )


def _find_ah_pattern(
    entries: list[MatchSnapshot],
    match_id: str,
    on_notification_sent: NotificationSentCallback | None = None,
    shutdown_requested: Event | None = None,
) -> bool:
    """Проверить паттерн закрытия линии азиатской форы."""
    return _find_market_pattern(
        entries,
        match_id,
        "ah",
        _send_ah_notification,
        on_notification_sent,
        shutdown_requested,
    )


def find_pattern_matches(
    match_history: MatchHistoryMap,
    on_notification_sent: NotificationSentCallback | None = None,
    shutdown_requested: Event | None = None,
) -> list[str]:
    """Проанализировать истории и вернуть ID матчей с найденным паттерном."""
    sent_matches = []

    for match_id, data in match_history.items():
        if shutdown_requested is not None and shutdown_requested.is_set():
            break
        entries = _collect_match_entries(data)

        if shutdown_requested is None:
            over_matched = _find_over_pattern(
                entries, match_id, on_notification_sent
            )
        else:
            over_matched = _find_over_pattern(
                entries, match_id, on_notification_sent, shutdown_requested
            )
        if over_matched:
            sent_matches.append(match_id)

        if shutdown_requested is None:
            ah_matched = _find_ah_pattern(
                entries, match_id, on_notification_sent
            )
        else:
            ah_matched = _find_ah_pattern(
                entries, match_id, on_notification_sent, shutdown_requested
            )
        if ah_matched:
            sent_matches.append(match_id)

    return sent_matches
