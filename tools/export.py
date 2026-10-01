import csv
import json
import os
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[1]
PREFERRED_COLUMNS = (
    'date',
    'mark',
    'league',
    'home_team',
    'away_team',
    'prediction',
    'odds',
    'final_score',
    'result',
    'link',
    'source',
)


def export_matches_to_csv():
    configured_path = os.environ.get('MATCHES_FILE')
    matches_path = Path(configured_path) if configured_path else PROJECT_DIR / 'matches.json'
    if not matches_path.is_absolute():
        matches_path = Path.cwd() / matches_path
    csv_path = matches_path.with_suffix('.csv')

    with matches_path.open('r', encoding='utf-8') as matches_file:
        matches = json.load(matches_file)

    if not isinstance(matches, list) or any(not isinstance(match, dict) for match in matches):
        raise ValueError(f'Expected a JSON list of match objects in {matches_path}')

    export_rows = [
        {column: match.get(column) for column in PREFERRED_COLUMNS}
        for match in matches
    ]

    with csv_path.open('w', encoding='utf-8-sig', newline='') as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=PREFERRED_COLUMNS, delimiter=';')
        writer.writeheader()
        writer.writerows(export_rows)

    return len(export_rows), csv_path


def main():
    try:
        match_count, csv_path = export_matches_to_csv()
    except (OSError, json.JSONDecodeError, ValueError) as error:
        raise SystemExit(f'Could not export matches: {error}') from error

    print(f'Exported {match_count} matches to {csv_path}')


if __name__ == '__main__':
    main()