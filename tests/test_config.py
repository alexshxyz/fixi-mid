import importlib
from contextlib import contextmanager

import pytest

from app import config


@contextmanager
def config_with_environment(monkeypatch, **variables):
    try:
        with monkeypatch.context() as environment:
            for name, value in variables.items():
                environment.setenv(name, value)
            yield importlib.reload(config)
    finally:
        importlib.reload(config)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("1", True),
        ("true", True),
        (" YES ", True),
        ("On", True),
        ("0", False),
        ("false", False),
        ("no", False),
        ("off", False),
    ],
)
def test_browser_headless_parses_environment_value(monkeypatch, value, expected):
    with config_with_environment(monkeypatch, BROWSER_HEADLESS=value) as settings:
        assert settings.BROWSER_HEADLESS is expected


def test_matches_file_can_be_overridden_with_environment(monkeypatch, tmp_path):
    matches_file = tmp_path / "custom-matches.json"

    with config_with_environment(monkeypatch, MATCHES_FILE=str(matches_file)) as settings:
        assert settings.MATCHES_FILE == str(matches_file)


def test_default_match_data_paths_use_data_directory(monkeypatch):
    monkeypatch.delenv("MATCHES_FILE", raising=False)
    with config_with_environment(monkeypatch) as settings:
        assert settings.DATA_DIR == settings.PROJECT_DIR / "data"
        assert settings.MATCHES_FILE == str(settings.DATA_DIR / "matches.json")
        assert settings.LEAGUES_FILE == str(settings.DATA_DIR / "leagues.json")
        assert settings.STATE_SAVE_FILE == str(settings.DATA_DIR / "match_state.json")


def test_table_live_reload_interval_is_generated_each_time(monkeypatch):
    generated_values = iter((103, 118))
    calls = []

    def randint(lower, upper):
        calls.append((lower, upper))
        return next(generated_values)

    monkeypatch.setattr(config.random, "randint", randint)

    assert config.get_table_live_reload_interval() == 103
    assert config.get_table_live_reload_interval() == 118
    assert calls == [(100, 120), (100, 120)]


def test_telegram_proxies_are_configured_with_encoded_credentials(monkeypatch):
    with config_with_environment(
        monkeypatch,
        TELEGRAM_PROXY_HOST="proxy.example",
        TELEGRAM_PROXY_PORT="1080",
        TELEGRAM_PROXY_USERNAME="user name",
        TELEGRAM_PROXY_PASSWORD="p@ss",
    ) as settings:
        proxy_url = "socks5h://user%20name:p%40ss@proxy.example:1080"
        assert settings.TELEGRAM_PROXIES == {
            "http": proxy_url,
            "https": proxy_url,
        }


def test_telegram_proxies_are_disabled_without_host_or_port(monkeypatch):
    with config_with_environment(
        monkeypatch,
        TELEGRAM_PROXY_HOST="proxy.example",
        TELEGRAM_PROXY_PORT="",
        TELEGRAM_PROXY_USERNAME="user",
        TELEGRAM_PROXY_PASSWORD="password",
    ) as settings:
        assert settings.TELEGRAM_PROXIES is None


def test_browser_proxy_server_can_be_overridden_with_environment(monkeypatch):
    with config_with_environment(
        monkeypatch, BROWSER_PROXY_SERVER="socks5://proxy.example:1080"
    ) as settings:
        assert settings.BROWSER_PROXY_SERVER == "socks5://proxy.example:1080"


def test_browser_proxy_server_does_not_use_a_code_default(monkeypatch):
    with config_with_environment(monkeypatch, BROWSER_PROXY_SERVER="") as settings:
        assert settings.BROWSER_PROXY_SERVER == ""


def test_site_cookies_have_expected_names_and_domain():
    expected_names = {
        "goaloo_SelCompany_V2",
        "orderby",
        "Default_TimeZone",
        "isOddsShow",
        "OddsShowType",
        "goalWindowCheck",
        "redWindowCheck",
        "YellowCheck",
    }

    assert {cookie["name"] for cookie in config.SITE_COOKIES} == expected_names
    assert all(cookie["domain"] == config.SITE_COOKIE_DOMAIN for cookie in config.SITE_COOKIES)
    assert all(cookie["path"] == "/" for cookie in config.SITE_COOKIES)


def test_application_settings_are_centralized():
    assert config.FINISHED_TIME_MARKERS == ("90", "90+", "FT", "ET", "Penalty")
    assert config.ERROR_SCREENSHOTS_DIR == config.PROJECT_DIR / "logs" / "screenshots"
    assert config.BOT_LOG_FILE == config.PROJECT_DIR / "bot.log"
