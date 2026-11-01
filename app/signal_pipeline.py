from threading import Event

from .config import Settings
from . import detector
from .logger import setup_logger
from .match_types import (
    MatchHistoryMap,
    MatchSignal,
    NotificationSentCallback,
    NotificationSentDetails,
    NotificationSentRegistrar,
)
from .notifier import send_telegram_notification

logger = setup_logger(__name__)


def _notification_sent_callback(
    signal: MatchSignal,
    on_notification_sent: NotificationSentCallback | None,
) -> NotificationSentRegistrar | None:
    """Связать callback успешной отправки с сигналом матча."""
    if on_notification_sent is None:
        return None

    def register(
        message_id: str | int,
        message: str,
        link: str,
        prediction: str,
        channel_id: str | None,
    ) -> None:
        details: NotificationSentDetails = {
            "match_id": signal["match_id"],
            "market": signal["market"],
            "league": signal["entry"].get("league", "Unknown"),
            "message_id": message_id,
            "message": message,
            "link": link,
            "prediction": prediction,
            "channel_id": channel_id,
        }
        on_notification_sent(details)

    return register


def _send_signal(
    signal: MatchSignal,
    on_notification_sent: NotificationSentCallback | None,
    shutdown_requested: Event | None,
    settings: Settings | None,
) -> None:
    """Передать найденный сигнал транспортному модулю."""
    entry = signal["entry"]
    on_sent_details = (
        None
        if signal["strategy"] == "without closed"
        else _notification_sent_callback(signal, on_notification_sent)
    )
    try:
        if signal["market"] == "ov":
            send_telegram_notification(
                league=entry.get("league", "Unknown"),
                home_team=entry.get("home_team", "Unknown"),
                away_team=entry.get("away_team", "Unknown"),
                score=entry.get("score", "Unknown"),
                over=signal["line"],
                over_odds=signal["odds"],
                drop_type=signal["drop_type"],
                match_id=signal["match_id"],
                match_time=entry["match_time"],
                on_sent_details=on_sent_details,
                strategy=signal["strategy"],
                shutdown_requested=shutdown_requested,
                settings=settings,
            )
        else:
            handicap = signal["line"]
            if (
                handicap
                and not handicap.startswith("-")
                and detector.ah_line_sign(handicap) != -1
            ):
                handicap = f"-{handicap}"
            send_telegram_notification(
                league=entry.get("league", "Unknown"),
                home_team=entry.get("home_team", "Unknown"),
                away_team=entry.get("away_team", "Unknown"),
                score=entry.get("score", "Unknown"),
                over_odds=signal["odds"],
                handicap_text=handicap,
                handicap_team_order=(
                    "Home" if signal["odds_side"] == "home" else "Away"
                ),
                drop_type=signal["drop_type"],
                match_id=signal["match_id"],
                match_time=entry["match_time"],
                on_sent_details=on_sent_details,
                strategy=signal["strategy"],
                shutdown_requested=shutdown_requested,
                settings=settings,
            )
    except Exception as error:
        logger.error(
            "Match %s: Failed to send notification: %s",
            signal["match_id"],
            error,
        )


def process_match_history(
    matches_history: MatchHistoryMap,
    on_notification_sent: NotificationSentCallback | None = None,
    shutdown_requested: Event | None = None,
    settings: Settings | None = None,
) -> list[str]:
    """Найти сигналы, отправить их и вернуть ID совпавших матчей."""
    signals = detector.find_pattern_matches(
        matches_history,
        shutdown_requested=shutdown_requested,
    )
    matched_ids: list[str] = []
    for signal in signals:
        if shutdown_requested is not None and shutdown_requested.is_set():
            break
        matched_ids.append(signal["match_id"])
        _send_signal(signal, on_notification_sent, shutdown_requested, settings)
    return matched_ids
