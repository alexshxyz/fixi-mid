import re
from threading import Event

from app import config
from app.config import FINISHED_TIME_MARKERS, START_ODD, THRESHOLD
from .matchtypes import (
    HandicapMarketLine,
    MatchSignal,
    MarketType,
    MarketValue,
    MatchHistory,
    MatchHistoryMap,
    MatchSnapshot,
    OddsSide,
    OverMarketLine,
)


def _odds_to_float(value: MarketValue) -> float | None:
    """Преобразовать коэффициент в число или вернуть None."""
    if value is None:
        return None
    try:
        return float(value)
    except (ValueError, TypeError):
        return None


def _is_exact_zero_ah_line(value: MarketValue) -> bool:
    """Проверить, обозначает ли значение нулевую фору."""
    return value is not None and str(value) in {"0", "0.0", "+0", "-0", "-0.0"}


def _is_ah_line_exception(value: MarketValue) -> bool:
    """Распознать фору исключение. (например 0/-0.5)"""
    if value is None:
        return False
    return re.fullmatch(r"\+?0/-[\d.,]+", str(value)) is not None


def ah_line_sign(value: MarketValue) -> int | None:
    """Определить принадлежность форы к команде."""
    if value is None:
        return None
    text = str(value)
    if not text:
        return None
    if _is_exact_zero_ah_line(text):
        return 0
    if _is_ah_line_exception(text):
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


def _line_status(entry: MatchSnapshot, market: MarketType) -> str:
    """Получить значение линии рынка из снимка матча."""
    if market == "ov":
        return entry["ov"]["over"]
    return entry["ah"]["ah"]


def _get_last_entry_before_closed(
    entries: list[MatchSnapshot],
    market: MarketType,
) -> tuple[MatchSnapshot | None, int]:
    """Найти последний снимок до закрытия линии указанного рынка."""
    for idx in range(len(entries) - 2, -1, -1):
        if _line_status(entries[idx], market) != "Closed":
            return entries[idx], idx
    return None, -1


def _match_scores(
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
    time_text = entry["match_time"].casefold()
    return any(marker.casefold() in time_text for marker in FINISHED_TIME_MARKERS)


def _get_market_values(
    entry: MatchSnapshot,
    market: MarketType,
) -> tuple[str, float | None, OddsSide | None]:
    """Получить линию, коэффициент и при необходимости сторону команды."""
    if market == "ov":
        over_data: OverMarketLine = entry["ov"]
        return (
            over_data["over"],
            _odds_to_float(over_data.get("over_odds")),
            None,
        )

    handicap_data: HandicapMarketLine = entry["ah"]
    handicap = handicap_data["ah"]
    sign = ah_line_sign(handicap)
    if sign in (None, 0):
        return handicap, None, None

    odds_side: OddsSide = "home" if sign > 0 else "away"
    odds = (
        handicap_data.get("home_ah_odds")
        if odds_side == "home"
        else handicap_data.get("away_ah_odds")
    )
    return handicap, _odds_to_float(odds), odds_side


def _find_market_pattern(
    entries: list[MatchSnapshot],
    match_id: str,
    market: MarketType,
    shutdown_requested: Event | None = None,
    strategy: str = "default",
) -> MatchSignal | None:
    """Проверить паттерн падения коэффициента для выбранной стратегии."""
    if shutdown_requested is not None and shutdown_requested.is_set():
        return None
    if len(entries) < 2 or _is_finished_match(entries[-1]):
        return None

    last_entry: MatchSnapshot | None
    if strategy == "without closed":
        if _line_status(entries[-1], market) == "Closed":
            return None
        last_entry, last_idx = entries[-1], len(entries) - 1
    else:
        if _line_status(entries[-1], market) != "Closed":
            return None
        last_entry, last_idx = _get_last_entry_before_closed(entries, market)
    if last_entry is None:
        return None

    last_line, last_odds, odds_side = _get_market_values(last_entry, market)

    if last_odds is None or last_odds > THRESHOLD:
        return None

    start_search_idx = last_idx - 1
    for search_idx in range(start_search_idx, -1, -1):
        if shutdown_requested is not None and shutdown_requested.is_set():
            return None
        current_entry = entries[search_idx]

        if _line_status(current_entry, market) == "Closed":
            continue

        current_line, current_odds, _ = _get_market_values(current_entry, market)

        if current_line != last_line:
            break

        if current_odds is not None and current_odds >= START_ODD:
            if not _match_scores(entries[-1], current_entry):
                return None
            drop_type = f"ODDS {current_odds:.2f} -> {last_odds:.2f}"
            return {
                "match_id": match_id,
                "market": market,
                "entry": last_entry,
                "line": last_line,
                "odds": last_odds,
                "drop_type": drop_type,
                "odds_side": odds_side,
                "strategy": strategy,
            }

    return None


def _find_over_pattern(
    entries: list[MatchSnapshot],
    match_id: str,
    shutdown_requested: Event | None = None,
) -> MatchSignal | None:
    """Проверить паттерн закрытия линии Over."""
    return _find_market_pattern(
        entries,
        match_id,
        "ov",
        shutdown_requested,
    )


def _find_ah_pattern(
    entries: list[MatchSnapshot],
    match_id: str,
    shutdown_requested: Event | None = None,
) -> MatchSignal | None:
    """Проверить паттерн закрытия линии азиатской форы."""
    return _find_market_pattern(
        entries,
        match_id,
        "ah",
        shutdown_requested,
    )


def _find_over_pattern_without_closed(
    entries: list[MatchSnapshot],
    match_id: str,
    shutdown_requested: Event | None = None,
) -> MatchSignal | None:
    """Проверить паттерн Over без ожидания закрытия линии."""
    return _find_market_pattern(
        entries,
        match_id,
        "ov",
        shutdown_requested,
        strategy="without closed",
    )


def _find_ah_pattern_without_closed(
    entries: list[MatchSnapshot],
    match_id: str,
    shutdown_requested: Event | None = None,
) -> MatchSignal | None:
    """Проверить паттерн азиатской форы без ожидания закрытия линии."""
    return _find_market_pattern(
        entries,
        match_id,
        "ah",
        shutdown_requested,
        strategy="without closed",
    )


def find_pattern_matches(
    matches_history: MatchHistoryMap,
    shutdown_requested: Event | None = None,
) -> list[MatchSignal]:
    """Проанализировать истории и вернуть найденные сигналы без отправки."""
    signals: list[MatchSignal] = []

    for match_id, data in matches_history.items():
        if shutdown_requested is not None and shutdown_requested.is_set():
            break
        entries = _collect_match_entries(data)

        if config.WITHOUT_CLOSED_STRATEGY_ACTIVE:
            signal = _find_over_pattern_without_closed(
                entries,
                match_id,
                shutdown_requested=shutdown_requested,
            )
            if signal is not None:
                signals.append(signal)

            signal = _find_ah_pattern_without_closed(
                entries,
                match_id,
                shutdown_requested=shutdown_requested,
            )
            if signal is not None:
                signals.append(signal)

        signal = _find_over_pattern(
            entries,
            match_id,
            shutdown_requested=shutdown_requested,
        )
        if signal is not None:
            signals.append(signal)

        signal = _find_ah_pattern(
            entries,
            match_id,
            shutdown_requested=shutdown_requested,
        )
        if signal is not None:
            signals.append(signal)

    return signals
