import json
import os
import tempfile
from contextlib import suppress
from datetime import date
from pathlib import Path
from typing import Any, cast
from urllib.parse import unquote, urlparse

from .config import MATCHES_FILE
from .logger import setup_logger
from .match_types import (
    LeagueStats,
    MatchNotificationState,
    MatchRecord,
    MarketType,
)

logger = setup_logger(__name__)

def _ensure_matches_file() -> Path:
    """Создать файл матчей и родительские каталоги, если их ещё нет."""
    path = Path(MATCHES_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text('[]\n', encoding='utf-8')
    return path


def _load_matches() -> list[MatchRecord]:
    """Загрузить сохранённые записи; сбросить повреждённый JSON."""
    path = _ensure_matches_file()
    try:
        with path.open('r', encoding='utf-8') as f:
            data: Any = json.load(f)
        if isinstance(data, list):
            return cast(list[MatchRecord], data)
        logger.error("Matches file does not contain a JSON list. Resetting %s.", path)
    except (json.JSONDecodeError, OSError, ValueError):
        logger.error("Matches file is empty or corrupted. Resetting %s.", path)

    with path.open('w', encoding='utf-8') as f:
        json.dump([], f, ensure_ascii=False, indent=2)
        f.write('\n')
    return []


def _save_matches(matches: list[MatchRecord]) -> None:
    """Атомарно сохранить записи матчей в JSON-файл."""
    path = _ensure_matches_file()
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode='w',
            encoding='utf-8',
            dir=path.parent,
            prefix=f'.{path.name}.',
            suffix='.tmp',
            delete=False,
        ) as f:
            temp_path = Path(f.name)
            json.dump(matches, f, ensure_ascii=False, indent=2)
            f.write('\n')
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp_path, path)
        temp_path = None
    except Exception:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
        raise


def init_storage() -> bool:
    """Создать хранилище матчей, если оно отсутствует."""
    _ensure_matches_file()
    return True


def check_duplicate_match(
    link: str | None,
    prediction: str | None,
) -> bool:
    """Проверить наличие записи с такими ссылкой и прогнозом."""
    if not link or not prediction:
        return False

    for item in _load_matches():
        if item.get('link') == link and item.get('prediction') == prediction:
            return True
    return False


def _get_match_id(item: MatchRecord) -> str | None:
    """Получить ID матча из поля записи или ссылки."""
    match_id = item.get('match_id')
    if match_id is not None:
        return str(match_id)

    link = item.get('link')
    if not link:
        return None
    path_parts = [part for part in urlparse(link).path.split('/') if part]
    return unquote(path_parts[-1]) if path_parts else None


def _get_market(item: MatchRecord) -> MarketType | None:
    """Определить рынок записи, включая старые записи без поля market."""
    market = item.get('market')
    if market == 'ov':
        return 'ov'
    if market == 'ah':
        return 'ah'

    prediction = str(item.get('prediction', ''))
    if prediction.startswith('Over '):
        return 'ov'
    if prediction.startswith('Handicap '):
        return 'ah'
    return None


def get_match_notification_states() -> dict[str, MatchNotificationState]:
    """Вернуть отправленные рынки для всех известных матчей."""
    states: dict[str, MatchNotificationState] = {}
    for item in _load_matches():
        if not isinstance(item, dict):
            continue
        match_id = _get_match_id(item)
        if match_id is None:
            continue

        state = states.setdefault(match_id, {'sent_markets': set()})
        market = _get_market(item)
        if market is not None:
            state['sent_markets'].add(market)

    return states


def get_match_notification_state(
    match_id: str | int | None,
) -> MatchNotificationState:
    """Вернуть множество отправленных рынков для одного матча."""
    return get_match_notification_states().get(
        str(match_id),
        {'sent_markets': set()},
    )


def check_duplicate_market(
    match_id: str | int | None,
    market: str,
) -> bool:
    """Проверить, отправлялось ли уведомление по этому рынку матча."""
    return market in get_match_notification_state(match_id)['sent_markets']


def update_match_mark(
    link: str | None,
    prediction: str | None,
    mark: str,
) -> bool:
    """Обновить отметку у сохранённого уведомления."""
    if not link or not prediction:
        return False

    matches = _load_matches()
    for index, match in enumerate(matches):
        if not isinstance(match, dict):
            continue
        if (
            match.get('link') == link
            and match.get('prediction') == prediction
        ):
            matches[index] = cast(
                MatchRecord,
                {
                    'mark': mark,
                    **{
                        key: value
                        for key, value in cast(dict[str, Any], match).items()
                        if key != 'mark'
                    },
                },
            )
            try:
                _save_matches(matches)
            except Exception as e:
                logger.error(f"Failed to update notification mark in matches.json: {e}")
                return False
            return True

    logger.warning(f"Could not find notification to update mark: {link} | {prediction}")
    return False


def save_match(
    league: str,
    home_team: str,
    away_team: str,
    prediction: str,
    odds: object,
    link: str,
    final_score: str | None = None,
    result: str | None = None,
    date_value: str | None = None,
    drop_type: str | None = None,
    match_id: str | int | None = None,
    market: str | None = None,
    strategy: str | None = 'default',
) -> tuple[int | None, int | None]:
    """Сохранить уведомление, если такой прогноз ещё не записан."""
    normalized_odds: float | None = None
    if odds is not None:
        with suppress(TypeError, ValueError):
            normalized_odds = float(cast(Any, odds))

    if date_value is None:
        date_value = date.today().isoformat()

    match_record: MatchRecord = {
        'mark': None,
        'league': league,
        'home_team': home_team,
        'away_team': away_team,
        'prediction': prediction,
        'drop_type': drop_type,
        'odds': normalized_odds,
        'final_score': final_score,
        'result': result,
        'link': link,
        'match_id': str(match_id) if match_id is not None else None,
        'market': market,
        'strategy': strategy or 'default',
        'date': date_value,
        'source': 'Crown',
    }

    matches = _load_matches()
    if check_duplicate_match(link, prediction):
        logger.info(f"Duplicate match found in matches.json: {link} | {prediction}")
        return None, None

    matches.append(match_record)
    _save_matches(matches)

    row_order = len(matches)
    return row_order, row_order


def get_all_matches() -> list[MatchRecord]:
    """Вернуть все сохранённые записи матчей."""
    return _load_matches()


def get_matches_in_date_range(
    start_date: str,
    end_date: str,
) -> list[MatchRecord]:
    """Вернуть записи за период, включая start_date и исключая end_date."""
    matches = _load_matches()
    filtered = []
    for match in matches:
        match_date = match.get('date')
        if match_date and start_date <= match_date < end_date:
            filtered.append(match)
    return filtered


def calculate_stats(matches: list[MatchRecord]) -> tuple[int, int, int, int]:
    """Подсчитать общее число матчей, побед, поражений и возвратов."""
    total = len(matches)
    wins = sum(1 for m in matches if m.get('result') == 'Won')
    losses = sum(1 for m in matches if m.get('result') == 'Lost')
    voids = sum(1 for m in matches if m.get('result') == 'Void')
    return total, wins, losses, voids


def get_stats_by_league(
    matches: list[MatchRecord],
) -> dict[str | None, LeagueStats]:
    """Сгруппировать статистику по лигам."""
    stats: dict[str | None, LeagueStats] = {}
    for match in matches:
        league = match.get('league', 'Unknown')
        if league not in stats:
            stats[league] = {
                'total': 0,
                'wins': 0,
                'losses': 0,
                'voids': 0,
            }
        stats[league]['total'] += 1
        if match.get('result') == 'Won':
            stats[league]['wins'] += 1
        elif match.get('result') == 'Lost':
            stats[league]['losses'] += 1
        elif match.get('result') == 'Void':
            stats[league]['voids'] += 1
    return stats
