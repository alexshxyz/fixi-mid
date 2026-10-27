import importlib
import json
import subprocess
import sys
from pathlib import Path

from app import config


PROJECT_MODULES = (
    "app.alarms",
    "app.closed_ov_ah",
    "app.config",
    "app.logger",
    "app.main",
    "app.notifier",
    "app.observer",
    "app.scripts",
    "app.storage",
    "app.state",
    "app.tracker",
    "app.match_types",
    "app.tools.export",
    "app.tools.monitor",
    "app.tools.stats_manual",
)

APP_PYTHON_FILES = {
    "__init__.py",
    "alarms.py",
    "closed_ov_ah.py",
    "config.py",
    "logger.py",
    "main.py",
    "notifier.py",
    "observer.py",
    "scripts.py",
    "storage.py",
    "state.py",
    "tracker.py",
    "match_types.py",
    "tools/__init__.py",
    "tools/export.py",
    "tools/monitor.py",
    "tools/stats_manual.py",
}


def test_application_modules_import_from_app_package():
    for module_name in PROJECT_MODULES:
        assert importlib.import_module(module_name).__name__ == module_name


def test_application_python_files_are_in_expected_directories():
    app_dir = config.PROJECT_DIR / "app"
    actual_files = {
        path.relative_to(app_dir).as_posix()
        for path in app_dir.rglob("*.py")
    }

    assert actual_files == APP_PYTHON_FILES


def test_match_data_directory_contains_data_not_python_modules():
    assert config.DATA_DIR == config.PROJECT_DIR / "data"
    assert config.MATCHES_FILE == str(config.DATA_DIR / "matches.json")
    assert config.LEAGUES_FILE == str(config.DATA_DIR / "leagues.json")
    assert config.STATE_SAVE_FILE == str(config.DATA_DIR / "match_state.json")
    assert not list(config.DATA_DIR.rglob("*.py"))

    leagues_path = config.DATA_DIR / "leagues.json"
    assert json.loads(leagues_path.read_text(encoding="utf-8")) == config.LEAGUES_LIST


def test_main_file_can_be_loaded_directly_from_another_working_directory(tmp_path):
    main_path = config.PROJECT_DIR / "app" / "main.py"
    command = (
        "import runpy; "
        f"runpy.run_path({str(main_path)!r})"
    )

    subprocess.run(
        [sys.executable, "-c", command],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
        timeout=15,
    )

def test_app_directory_does_not_shadow_standard_library_types():
    app_dir = config.PROJECT_DIR / "app"
    command = (
        "import sys; "
        f"sys.path.insert(0, {str(app_dir)!r}); "
        "sys.modules.pop('enum', None); "
        "sys.modules.pop('types', None); "
        "import enum"
    )

    subprocess.run(
        [sys.executable, "-S", "-c", command],
        check=True,
        capture_output=True,
        text=True,
        timeout=15,
    )
