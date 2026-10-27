from pathlib import Path


_SCRIPT_DIR = Path(__file__).parent / "js"


def _read_script(name):
    """Загрузить JavaScript-файл из каталога скриптов приложения."""
    return (_SCRIPT_DIR / name).read_text(encoding="utf-8").removesuffix("\n")


_LIVE_ODDS_READINESS_HELPER = _read_script("live_odds_readiness_helper.js")

# Устанавливает наблюдатель за изменениями Live-таблицы.
INSTALL_LIVE_CHANGE_OBSERVER = _read_script("install_live_change_observer.js")

# Отключает наблюдатель и очищает его флаги.
REMOVE_LIVE_CHANGE_OBSERVER = _read_script("remove_live_change_observer.js")

# Проверяет, изменился ли DOM Live-таблицы.
LIVE_CHANGE_PREDICATE = _read_script("live_change_predicate.js")

# Сбрасывает флаг изменения Live-таблицы.
RESET_LIVE_CHANGE_FLAG = _read_script("reset_live_change_flag.js")

# Проверяет наличие Crown и видимых коэффициентов после обновления.
LIVE_ODDS_READINESS = _read_script("live_odds_readiness.js").replace(
    "__LIVE_ODDS_READINESS_HELPER__",
    _LIVE_ODDS_READINESS_HELPER,
)

# Собирает видимые матчи и отмечает их готовность к наблюдению.
COLLECT_MATCH_STATUS = _read_script("collect_match_status.js").replace(
    "__LIVE_ODDS_READINESS_HELPER__",
    _LIVE_ODDS_READINESS_HELPER,
)

# Извлекает коэффициенты и сведения по переданным ID матчей.
EXTRACT_ALL_MATCH_DATA = _read_script("extract_all_match_data.js")
