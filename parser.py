"""
Основная логика парсинга одного матча.
Координация: загрузка страницы, инициализация браузера, вызов парсеров и отправка результатов.
"""
import json
import os
import time
import random
import logging

# Настройка логирования
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Handler для файла
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

match_history = {}
STATE_SAVE_FILE = "match_state.json"
RESTART_HOURS = 8  # Число часов до сохранения состояния и «рестарта»


def _save_state_to_json(active_match_ids, last_data, path=STATE_SAVE_FILE):
    try:
        payload = {
            "match_history": match_history,
            "last_data": last_data,
            "active_match_ids": active_match_ids,
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        logger.info(f"Saved state to {path}")
        return True
    except Exception as e:
        logger.error(f"Failed to save state to {path}: {e}")
        return False


def load_state_from_json(path=STATE_SAVE_FILE):
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            saved_state = json.load(f)
        os.remove(path)
        logger.info(f"Loaded and removed saved state file {path}")
        return saved_state
    except Exception as e:
        logger.error(f"Failed to load state from {path}: {e}")
        return None
    

def _extract_all_match_data(page, match_ids):
    """
    Извлекает данные ВСЕ матчей за один evaluate() вызов.
    Вместо 70 evaluate, делаем 1 — это главная оптимизация.
    """
    js = """
        (matchIds) => {
            const result = {};
            
            for (const match_id of matchIds) {
                const timeElem = document.querySelector('td#time_' + match_id);
                if (!timeElem) {
                    result[match_id] = null;
                    continue;
                }
                
                const row = timeElem.closest('tr');
                if (!row) {
                    result[match_id] = null;
                    continue;
                }
                
                const tds = Array.from(row.querySelectorAll('td.oddstd'));
                if (tds.length < 3) {
                    result[match_id] = null;
                    continue;
                }
                
                const normalize = (el) => {
                    if (!el) return '-';
                    const value = el.textContent.trim();
                    return value === '' ? '-' : value;
                };
                
                const odds1 = [];
                const odds3 = [];
                
                tds.slice(0, 3).forEach(td => {
                    odds1.push(normalize(td.querySelector('p.odds1')));
                    odds3.push(normalize(td.querySelector('p.odds3')));
                });
                
                const timeTd = document.querySelector('td#time_' + match_id);
                const onclick = timeTd.getAttribute('onclick');
                let league = 'Unknown';
                let team1 = row.querySelector('a[id="team1_' + match_id + '"]')?.textContent.trim() || 'Unknown';
                let team2 = row.querySelector('a[id="team2_' + match_id + '"]')?.textContent.trim() || 'Unknown';
                
                if (onclick) {
                    const match = onclick.match(/soccerInPage\\.detail\\([^,]+,"([^"]*)","([^"]*)","([^"]*)"\\)/);
                    if (match) {
                        team1 = match[1] || team1;
                        team2 = match[2] || team2;
                        league = match[3] || league;
                    }
                }
                
                result[match_id] = {
                    time: timeElem.textContent.trim() || 'Unknown',
                    ah: {
                        home_ah_odds: odds1[0],
                        ah: odds1[1],
                        away_ah_odds: odds1[2]
                    },
                    ov: {
                        over_odds: odds3[0],
                        over: odds3[1],
                        under_odds: odds3[2]
                    },
                    team1: team1,
                    team2: team2,
                    score: row.querySelector('td.f-b.blue.handpoint')?.textContent.trim() || 'Unknown',
                    league: league
                };
            }
            
            return result;
        }
    """
    
    return page.evaluate(js, match_ids)


def _collect_match_ids(page):
    return page.evaluate("""
        () => {
            const matches = [];
            const timeElements = Array.from(document.querySelectorAll('[id^=\"time_\"]'));

            for (const timeElem of timeElements) {
                if (timeElem.offsetParent === null) continue;
                const matchId = timeElem.id.replace(/^time_/, '');
                if (!matchId) continue;

                const row = timeElem.closest('tr');
                if (!row) continue;

                const hasOdds = row.querySelector('p.odds1, p.odds2') !== null;
                if (hasOdds) {
                    matches.push(matchId);
                }
            }

            return matches;
        }
    """)


def parse_and_monitor_match(page, match_ids=None, saved_state=None):
    """
    Парсит и мониторит все матчи по списку ID.
    Сохраняет начальные и измененные данные в памяти.
    """
    last_data = {}
    active_match_ids = []

    try:
        if saved_state:
            active_match_ids = saved_state.get("active_match_ids", [])
            restored_history = saved_state.get("match_history", {})
            match_history.clear()
            match_history.update(restored_history)
            last_data = saved_state.get("last_data", {})
            logger.info(f"Restored state for {len(active_match_ids)} matches from {STATE_SAVE_FILE}")
        else:
            active_match_ids = list(match_ids or [])
            logger.info(f"Initializing monitoring for {len(active_match_ids)} matches")

            # Инициализация начальных данных для всех матчей за один evaluate
            if active_match_ids:
                initial_all_data = _extract_all_match_data(page, active_match_ids)
                for match_id in active_match_ids:
                    initial_data = initial_all_data.get(match_id)
                    if initial_data:
                        match_history[match_id] = {
                            'initial': initial_data,
                            'changes': []
                        }
                        last_data[match_id] = {
                            'ah': initial_data['ah'],
                            'ov': initial_data['ov']
                        }
                        logger.info(f"Initial data loaded for match {match_id}")
                    else:
                        logger.info(f"No initial data for match {match_id}")

        reload_counter = 0
        reload_threshold = random.randint(50, 60)  # 100-120 секунд (каждая итерация = 2 секунды)
        restart_deadline = time.time() + RESTART_HOURS * 3600

        # Бесконечный цикл мониторинга
        while True:
            time.sleep(2)
            reload_counter += 1

            if time.time() >= restart_deadline:
                logger.info(f"Restart interval reached ({RESTART_HOURS} hours). Saving state before restart...")
                if _save_state_to_json(active_match_ids, last_data):
                    loaded_state = load_state_from_json()
                    if loaded_state:
                        active_match_ids = loaded_state.get("active_match_ids", [])
                        restored_history = loaded_state.get("match_history", {})
                        match_history.clear()
                        match_history.update(restored_history)
                        last_data = loaded_state.get("last_data", {})
                        logger.info("State restored from JSON and file removed. Continuing monitoring.")
                    else:
                        logger.error("Failed to reload saved state after restart. Continuing with current memory.")
                restart_deadline = time.time() + RESTART_HOURS * 3600
                reload_counter = 0
                reload_threshold = random.randint(25, 35)

            if reload_counter >= reload_threshold:
                reload_counter = 0
                reload_threshold = random.randint(25, 35)  # Новое случайное значение

                # Бесконечно пытаемся перезагрузить страницу
                while True:
                    try:
                        page.reload(wait_until='domcontentloaded')
                        page.wait_for_selector('table#table_live', timeout=30000)
                        logger.info("Page reloaded and table_live is ready")
                        break  # Успешно загружена, выходим из цикла
                    except Exception as e:
                        logger.error(f"Reload failed: {e}. Retrying in 3 seconds...")
                        time.sleep(3)
                        # Продолжаем цикл и пытаемся снова

                current_match_ids = _collect_match_ids(page)
                new_match_ids = [m for m in current_match_ids if m not in active_match_ids]
                removed_match_ids = [m for m in active_match_ids if m not in current_match_ids]

                for removed_id in removed_match_ids:
                    if removed_id in match_history:
                        del match_history[removed_id]
                    if removed_id in last_data:
                        del last_data[removed_id]
                    logger.info(f"Match {removed_id} removed")

                if new_match_ids:
                    new_data = _extract_all_match_data(page, new_match_ids)
                    for new_id in new_match_ids:
                        initial_data = new_data.get(new_id)
                        if initial_data:
                            match_history[new_id] = {
                                'initial': initial_data,
                                'changes': []
                            }
                            last_data[new_id] = {
                                'ah': initial_data['ah'],
                                'ov': initial_data['ov']
                            }
                            logger.info(f"New match {new_id} added")

                active_match_ids = current_match_ids

            # Один evaluate для всех матчей вместо циклов по отдельности
            if active_match_ids:
                all_match_data = _extract_all_match_data(page, active_match_ids)
                
                for match_id in active_match_ids:
                    if match_id not in last_data:
                        continue
                    current_data = all_match_data.get(match_id)
                    if current_data:
                        if (current_data['ah'] != last_data[match_id]['ah'] or
                            current_data['ov'] != last_data[match_id]['ov']):
                            match_history[match_id]['changes'].append(current_data)
                            last_data[match_id] = {
                                'ah': current_data['ah'],
                                'ov': current_data['ov']
                            }
                            logger.info(f"Match {match_id} updated")

            # Проверяем паттерны каждые 12 итераций или при изменениях
            if reload_counter == 0:
                from logics import find_pattern_matches
                find_pattern_matches(match_history)

    except Exception as e:
        logger.error(f"Error in parse_and_monitor_match: {e}")


# Экспорт функций
__all__ = ['parse_and_monitor_match']