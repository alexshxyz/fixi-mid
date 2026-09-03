import json
import os
import tempfile
from datetime import date
from pathlib import Path
from logging_config import setup_logger

logger = setup_logger(__name__)

APP_DIR = Path(__file__).resolve().parent
MATCHES_FILE = os.environ.get('MATCHES_FILE', str(APP_DIR / 'matches.json'))

def _ensure_matches_file():
    path = Path(MATCHES_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text('[]\n', encoding='utf-8')
    return path


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


def init_storage():
    """Создаёт файл matches.json, если его ещё нет."""
    _ensure_matches_file()
    return True


def check_duplicate_match(link, prediction):
    """
    Проверяет, есть ли уже запись с таким же link и prediction.
    Логика та же, что и раньше, но данные читаются из JSON файла.
    """
    if not link or not prediction:
        return False

    for item in _load_matches():
        if item.get('link') == link and item.get('prediction') == prediction:
            return True
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
    script=None,
    date_value=None,
):
    if odds is not None:
        try:
            odds = float(odds)
        except (TypeError, ValueError):
            odds = None

    if date_value is None:
        date_value = date.today().isoformat()

    match_record = {
        'league': league,
        'home_team': home_team,
        'away_team': away_team,
        'prediction': prediction,
        'odds': odds,
        'final_score': final_score,
        'result': result,
        'link': link,
        'script': script,
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


# Statistics functions for JSON-based data
def get_all_matches():
    """Get all matches from JSON file."""
    return _load_matches()


def get_matches_in_date_range(start_date, end_date):
    """Get matches within a date range (inclusive start, exclusive end)."""
    matches = _load_matches()
    filtered = []
    for match in matches:
        match_date = match.get('date')
        if match_date and start_date <= match_date < end_date:
            filtered.append(match)
    return filtered


def calculate_stats(matches):
    """
    Calculate statistics from a list of matches.
    Returns: (total, wins, losses, voids)
    """
    total = len(matches)
    wins = sum(1 for m in matches if m.get('result') == 'Won')
    losses = sum(1 for m in matches if m.get('result') == 'Lost')
    voids = sum(1 for m in matches if m.get('result') == 'Void')
    return total, wins, losses, voids


def get_stats_by_league(matches):
    """
    Get statistics grouped by league.
    Returns: dict with league as key and (total, wins, losses, voids) as value
    """
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
