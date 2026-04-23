import logging
from telegram_notifier import send_telegram_notification

# Настройка логирования
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Handler для файла
import os
log_file = os.path.join(os.path.dirname(__file__), 'bot.log')
file_handler = logging.FileHandler(log_file)
file_handler.setLevel(logging.INFO)
file_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
file_handler.setFormatter(file_formatter)

# Handler для консоли
console_handler = logging.StreamHandler()
console_handler.setLevel(logging.INFO)
console_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
console_handler.setFormatter(console_formatter)

logger.addHandler(file_handler)
logger.addHandler(console_handler)

THRESHOLD = 0.65  # Изменяй это значение для настройки порога
MAX_ODD = 0.80   # Минимальный коэффициент на ТБ для срабатывания паттерна


def _to_float(value):
    try:
        return float(value)
    except (ValueError, TypeError):
        return None


def _normalize_ah_text(value):
    if value is None:
        return None
    text = str(value).strip()
    return text if text else None


def _is_exact_zero_ah(value):
    text = _normalize_ah_text(value)
    return text in {"0", "0.0", "+0", "-0", "-0.0"}


def _is_away_zero_split_handicap(value):
    text = _normalize_ah_text(value)
    if text is None:
        return False
    # Example: 0/-0.5 — это фора второй команды
    return bool(__import__("re").match(r"^\+?0\s*/\s*-[\d.,]+$", text))


def _ah_sign(value):
    text = _normalize_ah_text(value)
    if text is None:
        return None
    if _is_exact_zero_ah(text):
        return 0
    if _is_away_zero_split_handicap(text):
        return -1
    return -1 if text.startswith("-") else 1


def _invert_handicap(value):
    text = _normalize_ah_text(value)
    if text is None:
        return None
    if text.startswith("-"):
        return text[1:]
    if text.startswith("+"):
        return f"-{text[1:]}"
    return f"-{text}"


def find_pattern_matches(match_history):
    sent_matches = []

    for match_id, data in match_history.items():
        entries = []
        if data.get("initial"):
            entries.append(data["initial"])
        entries.extend(data.get("changes", []))

        # ===== ЛОГИКА ДЛЯ OVER =====
        # 1. Поиск базовой строки (last)
        if len(entries) >= 2 and entries[-1].get("ov", {}).get("over") == "Closed":
            # Найти последний entry перед "Closed" с over != "Closed"
            last_entry = None
            for i in range(len(entries) - 2, -1, -1):
                if entries[i].get("ov", {}).get("over") != "Closed":
                    last_entry = entries[i]
                    break
            
            if last_entry is None:
                continue
            
            last_over_odds = _to_float(last_entry.get("ov", {}).get("over_odds"))
            last_total = last_entry.get("ov", {}).get("over")
            
            if last_over_odds is not None and last_over_odds <= THRESHOLD and last_total and last_total != "Closed":
                
                # 2. Поиск подходящих строк выше
                found = False
                start_search_idx = i - 1  # Начинать с строки выше last_entry
                for search_idx in range(start_search_idx, -1, -1):
                    current_entry = entries[search_idx]
                    
                    # Пропустить строки с "Closed"
                    if current_entry.get("ov", {}).get("over") == "Closed":
                        continue
                    
                    current_total = current_entry.get("ov", {}).get("over")
                    current_over_odds = _to_float(current_entry.get("ov", {}).get("over_odds"))
                    
                    # Если линия тотала изменилась — остановить поиск
                    if current_total != last_total:
                        break
                    
                    # Если совпадает и current_odd >= MAX_ODD
                    if current_over_odds is not None and current_over_odds >= MAX_ODD:
                        
                        # Отправляем уведомление с коэффициентом строки непосредственно перед Closed
                        team1 = last_entry.get("team1", "Unknown")
                        team2 = last_entry.get("team2", "Unknown")
                        score = last_entry.get("score", "Unknown")
                        league = last_entry.get("league", "Unknown")
                        over = last_total
                        over_odds = last_over_odds
                        
                        try:
                            send_telegram_notification(
                                league=league,
                                team1=team1,
                                team2=team2,
                                score=score,
                                over=over,
                                over_odds=over_odds,
                                match_id=match_id
                            )
                        except Exception as e:
                            logger.error(f"Match {match_id}: Failed to send notification: {e}")
                        
                        sent_matches.append(match_id)
                        found = True
                        break  # Завершить обработку матча
                
                if not found:
                    logger.debug(f"Match {match_id}: No matching entry found above")
            else:
                logger.debug(f"Match {match_id}: Base condition not met")
        else:
            logger.debug(f"Match {match_id}: No 'Closed' in last entry or insufficient entries")

        # ===== НОВАЯ ЛОГИКА ДЛЯ AH =====
        if len(entries) >= 2 and entries[-1].get("ah", {}).get("ah") == "Closed":
            last_entry = None
            for i in range(len(entries) - 2, -1, -1):
                if entries[i].get("ah", {}).get("ah") != "Closed":
                    last_entry = entries[i]
                    break

            if last_entry is not None:
                last_ah = last_entry.get("ah", {}).get("ah")
                raw_ah = last_ah
                sign = _ah_sign(raw_ah)
                last_ah_odds = None
                odds_side = None

                if sign is not None and sign != 0:
                    if sign > 0 and not _is_away_zero_split_handicap(raw_ah):
                        last_ah_odds = _to_float(last_entry.get("ah", {}).get("home_ah_odds"))
                        odds_side = "home"
                    else:
                        last_ah_odds = _to_float(last_entry.get("ah", {}).get("away_ah_odds"))
                        odds_side = "away"

                if last_ah and last_ah != "Closed" and last_ah_odds is not None and last_ah_odds <= THRESHOLD:
                    found = False
                    start_search_idx = i - 1
                    for search_idx in range(start_search_idx, -1, -1):
                        current_entry = entries[search_idx]
                        if current_entry.get("ah", {}).get("ah") == "Closed":
                            continue

                        current_ah = current_entry.get("ah", {}).get("ah")
                        if current_ah != last_ah:
                            break

                        if odds_side == "home":
                            current_ah_odds = _to_float(current_entry.get("ah", {}).get("home_ah_odds"))
                        else:
                            current_ah_odds = _to_float(current_entry.get("ah", {}).get("away_ah_odds"))

                        if current_ah_odds is not None and current_ah_odds >= MAX_ODD:
                            team1 = last_entry.get("team1", "Unknown")
                            team2 = last_entry.get("team2", "Unknown")
                            score = last_entry.get("score", "Unknown")
                            league = last_entry.get("league", "Unknown")
                            handicap = last_ah
                            handicap_order = "Home" if odds_side == "home" else "Away"

                            # Добавить "-" перед handicap, если его нет и это не away zero split
                            if handicap and not handicap.startswith("-") and not _is_away_zero_split_handicap(handicap):
                                handicap = f"-{handicap}"

                            send_telegram_notification(
                                league=league,
                                team1=team1,
                                team2=team2,
                                score=score,
                                match_id=match_id,
                                over_odds=last_ah_odds,
                                handicap_text=handicap,
                                handicap_team_order=handicap_order
                            )
                            sent_matches.append(match_id)
                            found = True
                            break

                    if not found:
                        pass
                else:
                    pass

    return sent_matches


if __name__ == "__main__":
    # Для тестирования, но в реальности match_history передается
    import json
    try:
        with open('matches_realtime.json', 'r', encoding='utf-8') as f:
            data = json.load(f)
        match_history = {}
        for match_id, match_data in data.items():
            match_history[match_id] = {
                'initial': match_data['initial'],
                'changes': match_data['changes']
            }
        logger.info(f"Loaded {len(match_history)} matches from matches_realtime.json")
    except Exception as e:
        logger.error(f"Failed to load matches_realtime.json: {e}")
        match_history = {}
    lines = find_pattern_matches(match_history)
    if lines:
        logger.info(f"Pattern found: {lines}")
    else:
        logger.info("No matches found")
