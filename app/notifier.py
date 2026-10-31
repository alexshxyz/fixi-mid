import requests
import re
import time
from decimal import Decimal, InvalidOperation, ROUND_CEILING
from threading import Event
from typing import Any

from .config import (
    BOT_TOKEN,
    CHANNEL_ID,
    TELEGRAM_REQUEST_TIMEOUT_SECONDS,
    TELEGRAM_RETRY_DELAY_SECONDS,
    TELEGRAM_SEND_MAX_ATTEMPTS,
    SITE_URL,
    TELEGRAM_API_URL as TELEGRAM_API_URL_TEMPLATE,
    TELEGRAM_PROXIES,
)
from .storage import (
    save_match,
    check_duplicate_market,
)
from .logger import setup_logger
from .match_types import MarketValue, NotificationSentRegistrar

logger = setup_logger(__name__)

TELEGRAM_API_URL = TELEGRAM_API_URL_TEMPLATE.format(token=BOT_TOKEN)
TELEGRAM_EDIT_MESSAGE_URL = TELEGRAM_API_URL.rsplit('/', 1)[0] + '/editMessageText'
NORMALIZED_HANDICAP_OUTCOMES = {
    ("Home", Decimal("-0.5")): "Победа 1",
    ("Away", Decimal("-0.5")): "Победа 2",
    ("Home", Decimal("0.5")): "1X",
    ("Away", Decimal("0.5")): "X2",
}


def _normalize_match_time_for_message(raw_time: str | None) -> str:
    """Нормализовать игровое время для сообщения Telegram."""
    if raw_time is None:
        return 'Unknown'

    value = str(raw_time).strip()
    if not value or value == 'Unknown':
        return 'Unknown'

    upper = value.upper()
    if upper == 'HT':
        return '45'
    if upper == 'FT':
        return 'FT'
    if upper == 'ET':
        return 'ET'
    if upper.startswith('ET+'):
        return value
    return value


def _normalize_split_value(
    value: MarketValue,
) -> MarketValue:
    """Усреднить дробную линию, сохранив знак исходного значения."""
    if value is None:
        return value

    text = str(value)
    parts = text.split("/")
    if len(parts) != 2:
        return value

    try:
        first, second = (Decimal(part.strip()) for part in parts)
    except InvalidOperation:
        return value

    average = (abs(first) + abs(second)) / 2
    normalized = format(average.normalize(), "f")
    if "." in normalized:
        normalized = normalized.rstrip("0").rstrip(".")

    sign = "-" if first.is_signed() or second.is_signed() else ""
    return f"{sign}{normalized}"


def _build_prediction(
    over: MarketValue,
    handicap_text: MarketValue,
    handicap_team_order: str | None,
) -> str:
    """Сформировать внутреннее название прогноза."""
    if handicap_text is None:
        return f"Over {_normalize_split_value(over)}"
    return (
        f"Handicap {_normalize_split_value(handicap_text)} "
        f"{handicap_team_order}"
    )


def _format_decimal(value: Decimal) -> str:
    """Убрать лишние завершающие нули из десятичного числа."""
    if value == 0:
        return "0"
    formatted = format(value.normalize(), "f")
    if "." in formatted:
        formatted = formatted.rstrip("0").rstrip(".")
    return formatted


def _format_handicap(value: Decimal) -> str:
    """Форматировать фору с явным плюсом для положительного значения."""
    formatted = _format_decimal(value)
    return f"+{formatted}" if value > 0 else formatted


def _round_quarter_total(value: Decimal) -> Decimal:
    """Округлить четвертной тотал вверх до целой или половинной линии."""
    fractional_part = abs(value) % 1
    if fractional_part in (Decimal("0.25"), Decimal("0.75")):
        return (value * 2).to_integral_value(rounding=ROUND_CEILING) / 2
    return value


def _round_quarter_handicap(value: Decimal) -> Decimal:
    """Округлить четвертную фору от нуля до целой или половинной линии."""
    fractional_part = abs(value) % 1
    if fractional_part not in (Decimal("0.25"), Decimal("0.75")):
        return value

    rounded_magnitude = (
        (abs(value) * 2).to_integral_value(rounding=ROUND_CEILING) / 2
    )
    return -rounded_magnitude if value.is_signed() else rounded_magnitude


def _parse_score(score: str | None) -> tuple[int, int] | None:
    """Разобрать счёт матча в количество голов команд."""
    if score is None:
        return None

    match = re.fullmatch(r"\s*(\d+)\s*[-:]\s*(\d+)\s*", str(score))
    if not match:
        return None
    return int(match.group(1)), int(match.group(2))


def _build_display_prediction(
    over: MarketValue,
    handicap_text: MarketValue,
    handicap_team_order: str | None,
    score: str | None,
) -> str:
    """Преобразовать прогноз в русское отображение для Telegram."""
    if handicap_text is None:
        total = _normalize_split_value(over)
        try:
            display_total: MarketValue = total
            display_total = _format_decimal(
                _round_quarter_total(Decimal(str(total)))
            )
        except (InvalidOperation, ValueError):
            logger.warning("Could not format total line for Telegram: %s", total)
            display_total = total
        return f"Тотал больше {display_total}"

    line = _normalize_split_value(handicap_text)
    if handicap_team_order not in ("Home", "Away"):
        logger.warning(
            "Could not map handicap team order for Telegram: %s",
            handicap_team_order,
        )
        return f"Фора {line} {handicap_team_order}"
    team_number = {"Home": "1", "Away": "2"}[handicap_team_order]

    try:
        handicap = Decimal(str(line))
    except (InvalidOperation, ValueError):
        logger.warning("Could not parse handicap line for Telegram: %s", line)
        return f"Фора {team_number} {line}"

    parsed_score = _parse_score(score)
    if parsed_score is None:
        logger.warning(
            "Could not recalculate handicap for Telegram with unknown score: %s",
            score,
        )
        return f"Фора {team_number} {_format_handicap(handicap)}"

    home_score, away_score = parsed_score
    score_difference = (
        home_score - away_score
        if handicap_team_order == "Home"
        else away_score - home_score
    )
    display_handicap = handicap - score_difference
    display_handicap = _round_quarter_handicap(display_handicap)

    normalized_outcome = NORMALIZED_HANDICAP_OUTCOMES.get(
        (handicap_team_order, display_handicap)
    )
    if normalized_outcome is not None:
        return normalized_outcome

    return f"Фора {team_number} {_format_handicap(display_handicap)}"


def _build_message(
    league: str,
    home_team: str,
    away_team: str,
    score: str | None,
    match_url: str,
    prediction: str,
    match_time: str | None = 'Unknown',
) -> str:
    """Сформировать HTML-сообщение о матче."""
    clean_match_time = _normalize_match_time_for_message(match_time)

    # Make the team-name / score segment a hyperlink to the oddscomp detail page.
    # Telegram HTML parse mode supports <a href="...">...</a> for clickable text.
    match_text = f"{home_team} {score} {away_team}"
    linkified_match_text = f'<a href="{match_url}">{match_text}</a>' if match_url else match_text

    return (
        f"<b>{league}</b>\n"
        f"🔎 {clean_match_time}’ {linkified_match_text}\n\n"
        f"{prediction}"
    )


def _send_message(
    payload: dict[str, Any],
    match_id: str | int | None = None,
    success_message: str | None = None,
    api_url: str = TELEGRAM_API_URL,
    max_attempts: int = 1,
    retry_delay: float = TELEGRAM_RETRY_DELAY_SECONDS,
    shutdown_requested: Event | None = None,
) -> Any:
    """Отправить запрос к Telegram API и вернуть объект ответа."""
    for attempt in range(1, max_attempts + 1):
        if shutdown_requested is not None and shutdown_requested.is_set():
            return None
        try:
            response = requests.post(
                api_url,
                json=payload,
                proxies=TELEGRAM_PROXIES,
                timeout=TELEGRAM_REQUEST_TIMEOUT_SECONDS,
            )
            if response.status_code == 200:
                try:
                    telegram_response = response.json()
                except ValueError as e:
                    logger.error(
                        "Invalid Telegram API response on attempt %s/%s: %s",
                        attempt,
                        max_attempts,
                        e,
                    )
                else:
                    if not (
                        isinstance(telegram_response, dict)
                        and telegram_response.get("ok") is False
                    ):
                        if success_message:
                            logger.info(success_message)
                        else:
                            logger.info(
                                "Telegram notification sent for match %s",
                                match_id,
                            )
                        return telegram_response

                    logger.error(
                        "Telegram API rejected request on attempt %s/%s: %s",
                        attempt,
                        max_attempts,
                        telegram_response.get("description", telegram_response),
                    )
            else:
                logger.error(
                    "Failed to send Telegram notification on attempt %s/%s: %s",
                    attempt,
                    max_attempts,
                    response.text,
                )
        except Exception as e:
            logger.error(
                "Error sending Telegram notification on attempt %s/%s: %s",
                attempt,
                max_attempts,
                e,
            )

        if attempt < max_attempts:
            if shutdown_requested is None:
                time.sleep(retry_delay)
            elif shutdown_requested.wait(retry_delay):
                return None
    return None


def edit_telegram_notification(
    message_id: str | int,
    text: str,
    channel_id: str | None = CHANNEL_ID,
    shutdown_requested: Event | None = None,
) -> bool:
    """Отредактировать ранее отправленное сообщение Telegram."""
    payload = {
        "chat_id": channel_id or CHANNEL_ID,
        "message_id": message_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    return bool(
        _send_message(
            payload,
            success_message=f"Telegram notification {message_id} updated",
            api_url=TELEGRAM_EDIT_MESSAGE_URL,
            shutdown_requested=shutdown_requested,
        )
    )


def _save_notification(
    league: str,
    home_team: str,
    away_team: str,
    prediction: str,
    odds_value: MarketValue,
    drop_type: str | None,
    match_url: str,
    match_id: str | int | None,
    market: str,
    strategy: str,
) -> None:
    """Сохранить успешно отправленное уведомление."""
    try:
        save_match(
            league=league,
            home_team=home_team,
            away_team=away_team,
            prediction=prediction,
            odds=odds_value,
            drop_type=drop_type,
            link=match_url,
            match_id=match_id,
            market=market,
            strategy=strategy,
        )
    except Exception as db_error:
        logger.error(f"Failed to save match: {db_error}")


def send_telegram_notification(
    league: str,
    home_team: str,
    away_team: str,
    score: str | None,
    over: MarketValue = None,
    over_odds: MarketValue = None,
    match_id: str | int | None = None,
    handicap_text: MarketValue = None,
    handicap_team_order: str | None = None,
    match_time: str | None = 'Unknown',
    on_sent_details: NotificationSentRegistrar | None = None,
    channel_id: str | None = None,
    drop_type: str | None = None,
    strategy: str | None = None,
    shutdown_requested: Event | None = None,
) -> bool:
    """Отправить сигнал в Telegram и сохранить его при успехе."""
    # Отправляет уведомление о матче в Telegram канал.
    target_channel_id = channel_id or CHANNEL_ID
    match_url = f"{SITE_URL}oddscomp/{match_id}" if match_id else ""
    odds_value = over_odds
    prediction = _build_prediction(over, handicap_text, handicap_team_order)
    display_prediction = _build_display_prediction(
        over,
        handicap_text,
        handicap_team_order,
        score,
    )
    market = "ah" if handicap_text is not None else "ov"
    strategy = strategy or "default"
    message = _build_message(
        league,
        home_team,
        away_team,
        score,
        match_url,
        display_prediction,
        match_time=match_time,
    )

    payload = {
        "chat_id": target_channel_id,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }

    if check_duplicate_market(match_id, market):
        return False

    telegram_response = _send_message(
        payload,
        match_id,
        max_attempts=TELEGRAM_SEND_MAX_ATTEMPTS,
        retry_delay=TELEGRAM_RETRY_DELAY_SECONDS,
        shutdown_requested=shutdown_requested,
    )
    if not telegram_response:
        return False

    _save_notification(
        league,
        home_team,
        away_team,
        prediction,
        odds_value,
        drop_type,
        match_url,
        match_id,
        market,
        strategy,
    )
    result = telegram_response.get('result')
    telegram_message_id = (
        result.get('message_id') if isinstance(result, dict) else None
    )
    if telegram_message_id is not None:
        if on_sent_details:
            try:
                on_sent_details(
                    telegram_message_id,
                    message,
                    match_url,
                    prediction,
                    target_channel_id,
                )
            except Exception as callback_error:
                logger.error(f"Failed to register Telegram notification details: {callback_error}")
    elif on_sent_details:
        logger.error("Telegram response did not include a message_id for status tracking")
    return True
