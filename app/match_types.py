"""Общие типы снимков матчей и данных их рынков."""

from typing import (
    Callable,
    Dict,
    List,
    Literal,
    Optional,
    Set,
    TypedDict,
    Union,
)

MarketValue = Optional[Union[str, int, float]]
MarketType = Literal["ov", "ah"]
OddsSide = Literal["home", "away"]


class OverMarketLine(TypedDict, total=False):
    """Линия и коэффициенты рынка Over в одном снимке матча."""

    over: Optional[str]
    over_odds: MarketValue
    under_odds: MarketValue


class HandicapMarketLine(TypedDict, total=False):
    """Линия и коэффициенты азиатской форы в одном снимке матча."""

    ah: Optional[str]
    home_ah_odds: MarketValue
    away_ah_odds: MarketValue


class MatchSnapshot(TypedDict, total=False):
    """Данные матча и рынков, зафиксированные в один момент."""

    time: Optional[str]
    match_time: Optional[str]
    ah: HandicapMarketLine
    ov: OverMarketLine
    team1: str
    team2: str
    score: str
    league: str
    date: Optional[str]


class MatchHistory(TypedDict, total=False):
    """Исходный снимок и последующие изменения одного матча."""

    initial: Optional[MatchSnapshot]
    changes: List[MatchSnapshot]


MatchHistoryMap = Dict[str, MatchHistory]


class NotificationSentDetails(TypedDict):
    """Данные отправленного уведомления для callback."""

    match_id: str
    market: MarketType
    league: str
    message_id: Union[str, int]
    message: str
    link: str
    prediction: str
    channel_id: Optional[str]


NotificationSentCallback = Callable[[NotificationSentDetails], None]
NotificationSentRegistrar = Callable[
    [Union[str, int], str, str, str, Optional[str]],
    None,
]


class TelegramNotification(TypedDict, total=False):
    """Аргументы отправки уведомления через Telegram."""

    league: str
    team1: str
    team2: str
    score: Optional[str]
    over: MarketValue
    over_odds: MarketValue
    match_id: Optional[Union[str, int]]
    handicap_text: MarketValue
    handicap_team_order: Optional[str]
    match_time: Optional[str]
    on_sent_details: Optional[NotificationSentRegistrar]
    channel_id: Optional[str]
    drop_type: Optional[str]
    strategy: Optional[str]


class MatchRecord(TypedDict, total=False):
    """Сохранённая запись об отправленном уведомлении."""

    mark: Optional[str]
    league: str
    home_team: str
    away_team: str
    prediction: str
    drop_type: Optional[str]
    odds: Optional[float]
    final_score: Optional[str]
    result: Optional[str]
    link: Optional[str]
    match_id: Optional[str]
    market: Optional[str]
    strategy: str
    date: Optional[str]
    source: str


class MatchNotificationState(TypedDict):
    """Рынки, по которым для матча уже отправлены уведомления."""

    sent_markets: Set[MarketType]


class LeagueStats(TypedDict):
    """Итоги по одной лиге."""

    total: int
    wins: int
    losses: int
    voids: int
