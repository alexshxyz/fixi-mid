from typing import (
    Literal,
    Required,
    TypedDict,
)
from collections.abc import Callable

MarketValue = str | int | float | None
MarketType = Literal["ov", "ah"]
OddsSide = Literal["home", "away"]


class OverMarketLine(TypedDict, total=False):
    """Линия и коэффициенты рынка Over в одном снимке матча."""

    over: Required[str]
    over_odds: MarketValue
    under_odds: MarketValue


class HandicapMarketLine(TypedDict, total=False):
    """Линия и коэффициенты азиатской форы в одном снимке матча."""

    ah: Required[str]
    home_ah_odds: MarketValue
    away_ah_odds: MarketValue


class MatchSnapshot(TypedDict, total=False):
    """Данные матча и рынков, зафиксированные в один момент."""

    match_time: Required[str]
    ah: Required[HandicapMarketLine]
    ov: Required[OverMarketLine]
    home_team: str
    away_team: str
    score: str
    league: str
    date: str


class MatchHistory(TypedDict, total=False):
    """Исходный снимок и последующие изменения одного матча."""

    initial: MatchSnapshot
    changes: list[MatchSnapshot]


MatchHistoryMap = dict[str, MatchHistory]


class MatchSignal(TypedDict):
    """Найденный сигнал рынка, готовый к обработке pipeline."""

    match_id: str
    market: MarketType
    entry: MatchSnapshot
    line: str
    odds: float
    drop_type: str
    odds_side: OddsSide | None
    strategy: str


class NotificationSentDetails(TypedDict):
    """Данные отправленного уведомления для callback."""

    match_id: str
    market: MarketType
    league: str
    message_id: str | int
    message: str
    link: str
    prediction: str
    channel_id: str | None


NotificationSentCallback = Callable[[NotificationSentDetails], None]
NotificationSentRegistrar = Callable[
    [str | int, str, str, str, str | None],
    None,
]


class TelegramNotification(TypedDict, total=False):
    """Аргументы отправки уведомления через Telegram."""

    league: str
    home_team: str
    away_team: str
    score: str
    over: MarketValue
    over_odds: MarketValue
    match_id: str | int | None
    handicap_text: MarketValue
    handicap_team_order: str
    match_time: str
    on_sent_details: NotificationSentRegistrar | None
    channel_id: str | None
    drop_type: str
    strategy: str


class MatchRecord(TypedDict, total=False):
    """Сохранённая запись об отправленном уведомлении."""

    mark: str | None
    league: str
    home_team: str
    away_team: str
    prediction: str
    drop_type: str | None
    odds: float | None
    final_score: str | None
    result: str | None
    link: str
    match_id: str | None
    market: str | None
    strategy: str
    date: str | None
    source: str


class MatchNotificationState(TypedDict):
    """Рынки, по которым для матча уже отправлены уведомления."""

    sent_markets: set[MarketType]


class LeagueStats(TypedDict):
    """Итоги по одной лиге."""

    total: int
    wins: int
    losses: int
    voids: int
