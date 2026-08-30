"""
Сервис получения статистики матчей из matches.json.
Функции для расчёта и форматирования статистики по лигам и в целом.
"""
import sys
import os
from datetime import datetime

# Добавляем родительскую директорию в path для импорта модулей
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from storage import get_all_matches, get_matches_in_date_range, calculate_stats, get_stats_by_league


def get_full_stats():
    """
    Получает полную статистику со всех матчей в matches.json.
    
    Returns:
        str: отформатированный HTML-текст с общей статистикой
    """
    try:
        matches = get_all_matches()
        total_matches, wins, losses, voids = calculate_stats(matches)

        if total_matches > 0:
            # Рассчитываем WR (Win Rate) - исключаем Void
            matches_without_void = wins + losses
            if matches_without_void > 0:
                win_rate = int(wins * 100 / matches_without_void)
            else:
                win_rate = 0

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


def get_top_leagues():
    """
    Получает топ 3 лиги по количеству побед.
    
    Returns:
        str: отформатированный HTML-текст с топ лигами
    """
    try:
        matches = get_all_matches()
        league_stats = get_stats_by_league(matches)

        if not league_stats:
            return "🏆 TOP LEAGUES\n\nNo data available."

        # Сортируем по победам (убывающий порядок) и потерям (возрастающий порядок)
        sorted_leagues = sorted(
            league_stats.items(),
            key=lambda x: (-x[1]['wins'], x[1]['losses'])
        )[:3]

        stats_text = "🏆 TOP LEAGUES\n\n"
        emojis = ['1️⃣', '2️⃣', '3️⃣']
        for idx, (league, stats) in enumerate(sorted_leagues, 1):
            wins = stats['wins']
            losses = stats['losses']
            voids = stats['voids']

            # Рассчитываем WR - исключаем Void
            matches_without_void = wins + losses
            if matches_without_void > 0:
                win_rate = int(wins * 100 / matches_without_void)
            else:
                win_rate = 0

            emoji = emojis[idx - 1] if idx - 1 < len(emojis) else str(idx)
            stats_text += (
                f"{emoji} {league}\n"
                f"{wins}W / {losses}L / {voids}D — {win_rate}%\n\n"
            )
        return stats_text.rstrip()
    except Exception as e:
        print(f"[JSON] Ошибка при получении топ-лиг: {e}")
        return f"❌ Ошибка при получении статистики: {e}"


def get_worst_leagues():
    """
    Получает худшие 3 лиги по количеству побед.
    
    Returns:
        str: отформатированный HTML-текст с худшими лигами
    """
    try:
        matches = get_all_matches()
        league_stats = get_stats_by_league(matches)

        if not league_stats:
            return "⛔️ WORST LEAGUES\n\nNo data available."

        # Сортируем по победам (возрастающий порядок) и потерям (убывающий порядок)
        sorted_leagues = sorted(
            league_stats.items(),
            key=lambda x: (x[1]['wins'], -x[1]['losses'])
        )[:3]

        stats_text = "⛔️ WORST LEAGUES\n\n"
        emojis = ['1️⃣', '2️⃣', '3️⃣']
        for idx, (league, stats) in enumerate(sorted_leagues, 1):
            wins = stats['wins']
            losses = stats['losses']
            voids = stats['voids']

            # Рассчитываем WR - исключаем Void
            matches_without_void = wins + losses
            if matches_without_void > 0:
                win_rate = int(wins * 100 / matches_without_void)
            else:
                win_rate = 0

            emoji = emojis[idx - 1] if idx - 1 < len(emojis) else str(idx)
            stats_text += (
                f"{emoji} {league}\n"
                f"{wins}W / {losses}L / {voids}D — {win_rate}%\n\n"
            )
        return stats_text.rstrip()
    except Exception as e:
        print(f"[JSON] Ошибка при получении худших лиг: {e}")
        return f"❌ Ошибка при получении статистики: {e}"


def get_last_5_matches():
    """
    Получает последние 5 матчей из matches.json.
    
    Returns:
        list: список данных матчей
    """
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

                # Форматируем результат только смайликом
                result = match.get('result')
                if result == 'Won':
                    result_emoji = "✅"
                elif result == 'Lost':
                    result_emoji = "❌"
                elif result == 'Void':
                    result_emoji = "🔁"
                else:
                    result_emoji = "?"

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
    """
    Форматирует последние 5 матчей для вывода.
    
    Returns:
        str: отформатированный текст
    """
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


def get_match_details_by_index(index):
    """
    Получает подробную информацию о конкретном матче по индексу.
    
    Args:
        index: индекс матча в списке (0 для последнего)
        
    Returns:
        dict: информация о матче или None если не найден
    """
    try:
        matches = get_all_matches()

        if index < 0 or index >= len(matches):
            return None

        match = matches[-(index + 1)]  # Берем с конца (последний матч имеет индекс 0)

        # Преобразуем дату
        match_date = match.get('date')
        if match_date:
            try:
                if isinstance(match_date, str):
                    date_obj = datetime.strptime(match_date, '%Y-%m-%d')
                    formatted_date = date_obj.strftime('%d.%m.%y')
                else:
                    formatted_date = match_date.strftime('%d.%m.%y')
            except Exception as e:
                print(f"[STATS] Ошибка при преобразовании даты {match_date}: {e}")
                formatted_date = "?"
        else:
            formatted_date = "?"

        # Форматируем результат смайликом
        result_status = match.get('result')
        if result_status == 'Won':
            result_emoji = "✅"
        elif result_status == 'Lost':
            result_emoji = "❌"
        elif result_status == 'Void':
            result_emoji = "🔁"
        else:
            result_emoji = "?"

        return {
            'emoji': result_emoji,
            'league': match.get('league') or "?",
            'home_team': match.get('home_team') or "?",
            'away_team': match.get('away_team') or "?",
            'prediction': match.get('prediction') or "?",
            'final_score': match.get('final_score') or "?",
            'date': formatted_date,
            'link': match.get('link') or "#",
            'odds': match.get('odds')
        }
    except Exception as e:
        print(f"[JSON] Ошибка при получении деталей матча: {e}")
        return None


def get_this_month_stats():
    """
    Получает статистику матчей за текущий месяц из matches.json.
    
    Returns:
        str: отформатированный HTML-текст со статистикой за месяц
    """
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
            # Вычисляем WR (Win Rate) - исключаем Void
            matches_without_void = wins + losses
            if matches_without_void > 0:
                win_rate = int(wins * 100 / matches_without_void)
            else:
                win_rate = 0

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