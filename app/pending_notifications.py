import time
from typing import Any
from threading import Event
from collections.abc import MutableMapping

from .config import (
    LEAGUES_LIST,
    NOTIFICATION_CHECK_DELAY_SECONDS,
    TELEGRAM_EDIT_MAX_ATTEMPTS,
    TELEGRAM_EDIT_RETRY_DELAY_SECONDS,
)
from .logger import setup_logger
from .notifier import edit_telegram_notification
from .storage import update_match_mark

logger = setup_logger(__name__)


def register_pending_notification(
    pending_notifications: dict[str, dict[str, Any]],
    notification: Any,
) -> None:
    """Запланировать отложенную проверку отправленного уведомления."""
    message_id = str(notification['message_id'])
    notification['deadline'] = time.time() + NOTIFICATION_CHECK_DELAY_SECONDS
    pending_notifications[message_id] = notification
    logger.info(
        "Scheduled Telegram status check for match %s (%s) in %d seconds",
        notification['match_id'],
        notification['market'],
        NOTIFICATION_CHECK_DELAY_SECONDS,
    )


def process_due_notifications(
    pending_notifications: dict[str, dict[str, Any]],
    last_data: MutableMapping[str, Any],
    active_match_ids: list[str],
    shutdown_requested: Event | None = None,
) -> None:
    """Обработать истёкшие таймеры и обновить Telegram-сообщения."""
    now = time.time()
    for message_id, notification in list(pending_notifications.items()):
        if shutdown_requested is not None and shutdown_requested.is_set():
            return
        if notification['deadline'] > now:
            continue

        if 'edited_message' not in notification:
            match_id = str(notification['match_id'])
            market = notification['market']
            field_name = 'over' if market == 'ov' else 'ah'
            last_market_data = last_data.get(match_id, {}).get(market, {})
            closed_remains = last_market_data.get(field_name) == 'Closed'
            league_is_listed = notification['league'] in LEAGUES_LIST

            if league_is_listed:
                marker = '🔥' if closed_remains else '⭐️'
            else:
                marker = '🔒' if closed_remains else '💩'

            message = notification['message']
            notification['edited_message'] = message.replace(
                '🔎 ', f'{marker} ', 1
            )
            notification['mark'] = marker

        edit_arguments = (
            notification['message_id'],
            notification['edited_message'],
            notification.get('channel_id'),
        )
        edit_succeeded = edit_telegram_notification(
            *edit_arguments, shutdown_requested=shutdown_requested
        )
        if edit_succeeded:
            if (
                notification.get('link')
                and notification.get('prediction')
                and notification.get('mark')
                and not update_match_mark(
                    notification['link'],
                    notification['prediction'],
                    notification['mark'],
                )
            ):
                logger.error(
                    "Failed to persist Telegram mark for message %s",
                    message_id,
                )
            _remove_pending_notification(
                message_id,
                notification,
                pending_notifications,
                last_data,
                active_match_ids,
            )
        elif shutdown_requested is not None and shutdown_requested.is_set():
            return
        else:
            notification['edit_attempts'] = notification.get('edit_attempts', 0) + 1
            if notification['edit_attempts'] >= TELEGRAM_EDIT_MAX_ATTEMPTS:
                _remove_pending_notification(
                    message_id,
                    notification,
                    pending_notifications,
                    last_data,
                    active_match_ids,
                )
                logger.error(
                    "Giving up Telegram status edit for message %s after %s attempts",
                    message_id,
                    TELEGRAM_EDIT_MAX_ATTEMPTS,
                )
            else:
                notification['deadline'] = now + TELEGRAM_EDIT_RETRY_DELAY_SECONDS
                logger.warning(
                    "Retrying Telegram status edit for message %s in %s seconds",
                    message_id,
                    TELEGRAM_EDIT_RETRY_DELAY_SECONDS,
                )


def _remove_pending_notification(
    message_id: str,
    notification: dict[str, Any],
    pending_notifications: dict[str, dict[str, Any]],
    last_data: MutableMapping[str, Any],
    active_match_ids: list[str],
) -> None:
    """Удалить обработанное уведомление и ненужные данные матча."""
    del pending_notifications[message_id]
    discard_match_data_if_unused(
        str(notification['match_id']),
        pending_notifications,
        last_data,
        active_match_ids,
    )


def discard_match_data_if_unused(
    match_id: str,
    pending_notifications: dict[str, dict[str, Any]],
    last_data: MutableMapping[str, Any],
    active_match_ids: list[str],
) -> None:
    """Удалить последний снимок, если он больше не нужен."""
    if match_id in active_match_ids:
        return
    if any(
        str(item['match_id']) == match_id
        for item in pending_notifications.values()
    ):
        return
    last_data.pop(match_id, None)


def next_notification_timeout(
    pending_notifications: dict[str, dict[str, Any]],
) -> float | None:
    """Вычислить время ожидания до ближайшей проверки уведомления."""
    if not pending_notifications:
        return None
    earliest_deadline: float = min(
        item['deadline'] for item in pending_notifications.values()
    )
    return max(0, earliest_deadline - time.time())