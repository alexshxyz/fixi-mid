import json
import os
import tempfile
from datetime import date
from pathlib import Path
from urllib.parse import unquote, urlparse
from config import CHANNEL_ID, MATCHES_FILE
from logger import setup_logger

logger = setup_logger(__name__)

APP_DIR = Path(__file__).resolve().parent
# Создаёт файл матчей, если он отсутствует.
def _ensure_matches_file():
    path = Path(MATCHES_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text('[]\n', encoding='utf-8')
    return path


# Загружает список матчей из JSON-файла.
def _load_matches():
    path = _ensure_matches_file()
    try:
        with path.open('r', encoding='utf-8') as f:
            data = json.load(f)
        if isinstance(data, list):
            return data
    except (json.JSONDecodeError, OSError, ValueError):
        logger.warning(f"Matches file is empty or corrupted. Resetting {path}.")

    with path.open('w', encoding='utf-8') as f:
        json.dump([], f, ensure_ascii=False, indent=2)
        f.write('\n')
    return []


# Сохраняет список матчей в JSON-файл.
def _save_matches(matches):
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


# Создаёт файл matches.json, если его ещё нет.
def init_storage():
    _ensure_matches_file()
    return True


# Проверяет, есть ли уже запись с таким же link и prediction.
def check_duplicate_match(link, prediction, channel_id=CHANNEL_ID):
    if not link or not prediction:
        return False

    target_channel_id = channel_id or CHANNEL_ID
    for item in _load_matches():
        item_channel_id = item.get('channel_id', CHANNEL_ID)
        if (
            item.get('link') == link
            and item.get('prediction') == prediction
            and item_channel_id == target_channel_id
        ):
            return True
    return False


# Возвращает match_id записи из явного поля или ссылки на матч.
def _get_match_id(item):
    match_id = item.get('match_id')
    if match_id is not None:
        return str(match_id)

    link = item.get('link')
    if not link:
        return None
    path_parts = [part for part in urlparse(link).path.split('/') if part]
    return unquote(path_parts[-1]) if path_parts else None


# Определяет рынок записи с поддержкой старых записей без поля market.
def _get_market(item):
    market = item.get('market')
    if market in {'ov', 'ah'}:
        return market

    prediction = str(item.get('prediction', ''))
    if prediction.startswith('Over '):
        return 'ov'
    if prediction.startswith('Handicap '):
        return 'ah'
    return None


# Определяет семейство стратегии, включая записи, созданные до поля strategy.
def _get_strategy(item):
    strategy = item.get('strategy')
    if strategy in {'old', 'new'}:
        return strategy

    drop_type = str(item.get('drop_type') or '')
    if drop_type.startswith('LINE '):
        return 'new'
    return 'old'


# Возвращает отправленные рынки и семейства стратегий для всех матчей.
def get_match_notification_states():
    states = {}
    for item in _load_matches():
        if not isinstance(item, dict):
            continue

        match_id = _get_match_id(item)
        if match_id is None:
            continue

        state = states.setdefault(
            match_id,
            {'sent_markets': set(), 'strategies': set()},
        )
        market = _get_market(item)
        strategy = _get_strategy(item)
        if market is not None:
            state['sent_markets'].add(market)
        state['strategies'].add(strategy)

    return states


# Возвращает отправленные рынки и семейства стратегий для матча.
def get_match_notification_state(match_id):
    return get_match_notification_states().get(
        str(match_id),
        {'sent_markets': set(), 'strategies': set()},
    )


# Проверяет, отправлялся ли уже сигнал этого рынка по match_id.
def check_duplicate_market(match_id, market):
    return market in get_match_notification_state(match_id)['sent_markets']


# Обновляет эмодзи для сохранённого уведомления.
def update_match_mark(link, prediction, mark, channel_id=CHANNEL_ID):
    if not link or not prediction:
        return False

    target_channel_id = channel_id or CHANNEL_ID
    matches = _load_matches()
    for index, match in enumerate(matches):
        if not isinstance(match, dict):
            continue
        match_channel_id = match.get('channel_id', CHANNEL_ID)
        if (
            match.get('link') == link
            and match.get('prediction') == prediction
            and match_channel_id == target_channel_id
        ):
            matches[index] = {
                'mark': mark,
                **{key: value for key, value in match.items() if key != 'mark'},
            }
            try:
                _save_matches(matches)
            except Exception as e:
                logger.error(f"Failed to update notification mark in matches.json: {e}")
                return False
            return True

    logger.warning(f"Could not find notification to update mark: {link} | {prediction}")
    return False


def save_match(
    league,
    home_team,
    away_team,
    prediction,
    odds,
    link,
    final_score=None,
    result=None,
    date_value=None,
    channel_id=CHANNEL_ID,
    drop_type=None,
    match_id=None,
    market=None,
    strategy=None,
):
    target_channel_id = channel_id or CHANNEL_ID
    if odds is not None:
        try:
            odds = float(odds)
        except (TypeError, ValueError):
            odds = None

    if date_value is None:
        date_value = date.today().isoformat()

    match_record = {
        'mark': None,
        'league': league,
        'home_team': home_team,
        'away_team': away_team,
        'prediction': prediction,
        'drop_type': drop_type,
        'odds': odds,
        'final_score': final_score,
        'result': result,
        'link': link,
        'match_id': str(match_id) if match_id is not None else None,
        'market': market,
        'strategy': strategy,
        'date': date_value,
        'source': 'Crown',
        'channel_id': target_channel_id,
    }

    matches = _load_matches()
    if check_duplicate_match(link, prediction, target_channel_id):
        logger.info(f"Duplicate match found in matches.json: {link} | {prediction}")
        return None, None

    matches.append(match_record)
    _save_matches(matches)

    row_order = len(matches)
    return row_order, row_order


# Возвращает все матчи из JSON-файла.
def get_all_matches():
    return _load_matches()


# Возвращает матчи в указанном диапазоне дат.
def get_matches_in_date_range(start_date, end_date):
    matches = _load_matches()
    filtered = []
    for match in matches:
        match_date = match.get('date')
        if match_date and start_date <= match_date < end_date:
            filtered.append(match)
    return filtered


# Подсчитывает общую статистику по списку матчей.
# Возвращает кортеж: (total, wins, losses, voids).
def calculate_stats(matches):
    total = len(matches)
    wins = sum(1 for m in matches if m.get('result') == 'Won')
    losses = sum(1 for m in matches if m.get('result') == 'Lost')
    voids = sum(1 for m in matches if m.get('result') == 'Void')
    return total, wins, losses, voids


# Группирует статистику по лигам.
# Возвращает словарь: лига -> (total, wins, losses, voids).
def get_stats_by_league(matches):
    stats = {}
    for match in matches:
        league = match.get('league', 'Unknown')
        if league not in stats:
            stats[league] = {'total': 0, 'wins': 0, 'losses': 0, 'voids': 0}
        stats[league]['total'] += 1
        if match.get('result') == 'Won':
            stats[league]['wins'] += 1
        elif match.get('result') == 'Lost':
            stats[league]['losses'] += 1
        elif match.get('result') == 'Void':
            stats[league]['voids'] += 1
    return stats
