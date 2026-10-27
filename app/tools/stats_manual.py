"""
Сервис получения статистики матчей из matches.json.
Функции для расчёта и форматирования статистики по лигам и в целом.
"""
import sys
import os
from datetime import datetime

# Добавляем корень проекта в path для поддержки прямого запуска файла.
PROJECT_DIR = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
sys.path.insert(0, PROJECT_DIR)

from app.storage import get_all_matches, get_matches_in_date_range, calculate_stats, get_stats_by_league


def _calculate_win_rate(wins, losses):
    """Рассчитать процент побед без учёта возвратов."""
    matches_without_void = wins + losses
    return int(wins * 100 / matches_without_void) if matches_without_void else 0


def _result_emoji(result):
    """Вернуть эмодзи для результата матча."""
    return {
        'Won': "✅",
        'Lost': "❌",
        'Void': "🔁",
    }.get(result, "?")


def get_full_stats():
    """Сформировать HTML-сводку по всем сохранённым матчам."""
    try:
        matches = get_all_matches()
        total_matches, wins, losses, voids = calculate_stats(matches)

        if total_matches > 0:
            win_rate = _calculate_win_rate(wins, losses)

            # Рассчитываем Profit и ROI
            # Won = +0.8, Lost = -1, Void = 0
            profit = wins * 0.8 - losses * 1
            if total_matches > 0:
                roi = int(profit * 100 / total_matches)
            else:
                roi = 0

            stats_text = (
                f"📚 SUMMARY\n\n"
                f"💰 {roi}% ROI 📈 {win_rate}% WR\n\n"
                f"{total_matches} matches ({wins}W / {losses}L / {voids}D)"
            )
            return stats_text
        else:
            return "📚 SUMMARY\n\nNo data available."
    except Exception as e:
        print(f"[JSON] Ошибка при получении полной статистики: {e}")
        return f"❌ Ошибка при получении статистики: {e}"


def _get_league_summary(header, sort_key, error_label):
    """Сформировать краткую статистику по лигам с указанной сортировкой."""
    try:
        matches = get_all_matches()
        league_stats = get_stats_by_league(matches)

        if not league_stats:
            return f"{header}\n\nNo data available."

        sorted_leagues = sorted(league_stats.items(), key=sort_key)[:3]
        stats_text = f"{header}\n\n"
        emojis = ['1️⃣', '2️⃣', '3️⃣']
        for idx, (league, stats) in enumerate(sorted_leagues, 1):
            wins = stats['wins']
            losses = stats['losses']
            voids = stats['voids']
            win_rate = _calculate_win_rate(wins, losses)

            emoji = emojis[idx - 1] if idx - 1 < len(emojis) else str(idx)
            stats_text += (
                f"{emoji} {league}\n"
                f"{wins}W / {losses}L / {voids}D — {win_rate}%\n\n"
            )
        return stats_text.rstrip()
    except Exception as e:
        print(f"[JSON] Ошибка при получении {error_label}: {e}")
        return f"❌ Ошибка при получении статистики: {e}"


def get_top_leagues():
    """Сформировать список трёх лиг с наибольшим количеством побед."""
    return _get_league_summary(
        "🏆 TOP LEAGUES",
        lambda item: (-item[1]['wins'], item[1]['losses']),
        "топ-лиг",
    )


def get_worst_leagues():
    """Сформировать список трёх лиг с наименьшим количеством побед."""
    return _get_league_summary(
        "⛔️ WORST LEAGUES",
        lambda item: (item[1]['wins'], -item[1]['losses']),
        "худших лиг",
    )


def get_last_5_matches():
    """Вернуть данные о пяти последних сохранённых матчах."""
    try:
        matches = get_all_matches()

        if matches:
            # Берем последние 5 в обратном порядке (самые новые первыми)
            last_matches = matches[-5:][::-1]
            matches_data = []

            for match in last_matches:
                # Подготавливаем значения, заменяя пустые на "?"
                home_team = match.get('home_team') or "?"
                away_team = match.get('away_team') or "?"

                result = match.get('result')
                result_emoji = _result_emoji(result)

                # Сохраняем полные данные
                matches_data.append({
                    'league': match.get('league') or "?",
                    'home_team': home_team,
                    'away_team': away_team,
                    'prediction': match.get('prediction') or "?",
                    'final_score': match.get('final_score') or "?",
                    'result': result or "?",
                    'result_emoji': result_emoji,
                    'date': match.get('date'),
                    'link': match.get('link') or "#",
                    'odds': match.get('odds')
                })

            return matches_data
        else:
            return []
    except Exception as e:
        print(f"[JSON] Ошибка при получении последних матчей: {e}")
        return []


def format_last_5_matches(matches_data):
    """Отформатировать список последних матчей в текстовый блок."""
    if not matches_data:
        return "🕒 LAST 5 MATCHES\n\nNo recent matches available."

    text = "🕒 LAST 5 MATCHES\n\n"
    for match in matches_data:
        text += f"{match['result_emoji']} {match['league']}\n"
        text += f"{match['home_team']} vs {match['away_team']}\n"
        text += f"Prediction: {match['prediction']}\n"
        text += f"Score: {match['final_score']}\n"
        text += f"Date: {match['date']}\n"
        text += f"Odds: {match['odds']}\n\n"
    return text.rstrip()


def get_this_month_stats():
    """Сформировать сводку по матчам за текущий календарный месяц."""
    try:
        # Получаем текущий год и месяц
        now = datetime.now()
        current_year = now.year
        current_month = now.month

        # Получаем первый день текущего месяца и первый день следующего месяца
        first_day_current = datetime(current_year, current_month, 1).date().isoformat()
        if current_month == 12:
            first_day_next = datetime(current_year + 1, 1, 1).date().isoformat()
        else:
            first_day_next = datetime(current_year, current_month + 1, 1).date().isoformat()

        # Названия месяцев в именительном падеже
        month_names = {
            1: "JANUARY", 2: "FEBRUARY", 3: "MARCH", 4: "APRIL",
            5: "MAY", 6: "JUNE", 7: "JULY", 8: "AUGUST",
            9: "SEPTEMBER", 10: "OCTOBER", 11: "NOVEMBER", 12: "DECEMBER"
        }
        current_month_name = month_names.get(current_month, "UNKNOWN MONTH")

        # Получаем матчи за текущий месяц
        matches = get_matches_in_date_range(first_day_current, first_day_next)
        total_matches, wins, losses, voids = calculate_stats(matches)

        if total_matches > 0:
            win_rate = _calculate_win_rate(wins, losses)

            # Вычисляем прибыль (Won = +0.8, Lost = -1, Void = 0)
            profit = wins * 0.8 - losses * 1
            profit_str = f"+{profit:.2f}" if profit >= 0 else f"{profit:.2f}"

            stats_text = (
                f"📅 {current_month_name}\n\n"
                f"💰 {profit_str} units 📈 {win_rate}% WR\n\n"
                f"🎯 {total_matches} matches\n"
                f"{wins}W / {losses}L / {voids}D"
            )
            return stats_text
        else:
            return f"📅 {current_month_name}\n\nNo data available."
    except Exception as e:
        print(f"[JSON] Ошибка при получении статистики за месяц: {e}")
        return f"❌ Ошибка при получении статистики: {e}"


if __name__ == "__main__":
    print("Доступные варианты статистики:")
    print("1. Статистика за все время")
    print("2. Последние 5 матчей")
    print("3. Статистика за текущий месяц")
    print("4. Лучшие лиги")
    print("5. Худшие лиги")

    choice = input("Введите номер варианта: ").strip()

    if choice == "1":
        print(get_full_stats())
    elif choice == "2":
        matches = get_last_5_matches()
        print(format_last_5_matches(matches))
    elif choice == "3":
        print(get_this_month_stats())
    elif choice == "4":
        print(get_top_leagues())
    elif choice == "5":
        print(get_worst_leagues())
    else:
        print("Неверный выбор.")