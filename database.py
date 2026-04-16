import os
from datetime import date
from dotenv import load_dotenv

import psycopg2

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), '.env'))

DB_HOST = os.environ.get('PG_HOST')
DB_PORT = os.environ.get('PG_PORT')
DB_NAME = os.environ.get('PG_NAME')
DB_USER = os.environ.get('PG_USER')
DB_PASS = os.environ.get('PG_PASS')
TABLE_NAME = os.environ.get('PG_TABLE')

# Проверяем, что все необходимые переменные установлены
required_vars = [DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASS, TABLE_NAME]
if not all(required_vars):
    missing = [name for name, var in zip(['PG_HOST', 'PG_PORT', 'PG_NAME', 'PG_USER', 'PG_PASS', 'PG_TABLE'], required_vars) if not var]
    raise ValueError(f"Missing required environment variables: {', '.join(missing)}")


def _get_connection():
    return psycopg2.connect(
        host=DB_HOST,
        port=DB_PORT,
        dbname=DB_NAME,
        user=DB_USER,
        password=DB_PASS,
    )


def init_db():
    create_table_query = f"""
        CREATE TABLE IF NOT EXISTS {TABLE_NAME} (
            id SERIAL PRIMARY KEY,
            league TEXT,
            home_team TEXT,
            away_team TEXT,
            prediction TEXT,
            odds NUMERIC(5,2),
            final_score TEXT,
            result TEXT,
            link TEXT,
            script BOOLEAN,
            date DATE NOT NULL DEFAULT CURRENT_DATE,
            row_order SERIAL UNIQUE NOT NULL
        )
    """
    with _get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(create_table_query)
            conn.commit()


def check_duplicate_match(link, prediction):
    """
    Проверяет есть ли уже запись с такой же link и prediction.
    Если дубликат найден, возвращает True, иначе False.
    Оптимизирована для использования индекса idx_link_prediction.
    """
    if not link or not prediction:
        return False
    
    query = f"SELECT 1 FROM {TABLE_NAME} WHERE link = %s AND prediction = %s LIMIT 1"
    
    try:
        with _get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(query, (link, prediction))
                result = cur.fetchone()
                return result is not None
    except Exception as e:
        print(f"Error checking duplicate match: {e}")
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
        date_value = date.today()

    insert_query = f"""
        INSERT INTO {TABLE_NAME} (
            league,
            home_team,
            away_team,
            prediction,
            odds,
            final_score,
            result,
            link,
            script,
            date
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id, row_order
    """

    with _get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                insert_query,
                (
                    league,
                    home_team,
                    away_team,
                    prediction,
                    odds,
                    final_score,
                    result,
                    link,
                    script,
                    date_value,
                ),
            )
            inserted_id, row_order = cur.fetchone()
            conn.commit()
            return inserted_id, row_order
