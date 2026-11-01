import json
import subprocess
import sys
from contextlib import contextmanager

import pytest

from app import config


@contextmanager
def config_with_environment(monkeypatch, **variables):
    with monkeypatch.context() as environment:
        for name, value in variables.items():
            environment.setenv(name, value)
        environment.setattr(config, "load_dotenv", lambda **kwargs: None)
        yield config.load_settings()


def test_browser_headless_is_configured_in_code(monkeypatch):
    with config_with_environment(monkeypatch, BROWSER_HEADLESS="false") as settings:
        assert config.BROWSER_HEADLESS is True
        assert not hasattr(settings, "browser_headless")


def test_matches_file_is_not_overridden_by_environment(monkeypatch, tmp_path):
    matches_file = tmp_path / "custom-matches.json"

    with config_with_environment(
        monkeypatch, MATCHES_FILE=str(matches_file)
    ):
        assert config.MATCHES_FILE == str(config.DATA_DIR / "matches.json")


def test_default_match_data_paths_use_data_directory(monkeypatch):
    assert config.DATA_DIR == config.PROJECT_DIR / "data"
    assert config.MATCHES_FILE == str(config.DATA_DIR / "matches.json")
    assert config.LEAGUES_FILE == str(config.DATA_DIR / "leagues.json")
    assert config.STATE_SAVE_FILE == str(config.DATA_DIR / "match_state.json")


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
        assert settings.telegram_proxies == {
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
        assert settings.telegram_proxies is None


def test_browser_proxy_server_can_be_overridden_with_environment(monkeypatch):
    with config_with_environment(
        monkeypatch, BROWSER_PROXY_SERVER="socks5://proxy.example:1080"
    ) as settings:
        assert settings.browser_proxy_server == "socks5://proxy.example:1080"


def test_browser_proxy_server_is_an_empty_string_when_not_configured(monkeypatch):
    with config_with_environment(monkeypatch, BROWSER_PROXY_SERVER="") as settings:
        assert settings.browser_proxy_server == ""


def test_telegram_bot_settings_default_to_empty_strings(monkeypatch):
    with monkeypatch.context() as environment:
        for name in (
            "BOT_TOKEN",
            "CHANNEL_ID",
            "MONITOR_TELEGRAM_TOKEN",
            "MONITOR_CHAT_ID",
        ):
            environment.delenv(name, raising=False)
        environment.setattr(config, "load_dotenv", lambda **kwargs: None)
        settings = config.load_settings()

        assert settings.bot_token == ""
        assert settings.channel_id == ""
        assert settings.monitor_telegram_token == ""
        assert settings.monitor_chat_id == ""


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


def test_config_import_does_not_load_dotenv_or_read_leagues():
    command = (
        "import dotenv, json; "
        "calls = []; "
        "dotenv.load_dotenv = lambda **kwargs: calls.append('dotenv'); "
        "json.load = lambda *args, **kwargs: calls.append('json'); "
        "import app.config; "
        "assert calls == []"
    )
    subprocess.run(
        [sys.executable, "-c", command],
        cwd=config.PROJECT_DIR,
        check=True,
        capture_output=True,
        text=True,
        timeout=15,
    )


def test_load_settings_preserves_environment_values_and_monitor_fallbacks(
    monkeypatch,
):
    with config_with_environment(
        monkeypatch,
        BOT_TOKEN="bot-token",
        CHANNEL_ID="channel",
        MONITOR_TELEGRAM_TOKEN="",
        MONITOR_CHAT_ID="",
    ) as settings:
        assert settings.bot_token == "bot-token"
        assert settings.channel_id == "channel"
        assert settings.monitor_telegram_token == "bot-token"
        assert settings.monitor_chat_id == "channel"
        assert settings.telegram_api_url == (
            "https://api.telegram.org/botbot-token/sendMessage"
        )
        assert settings.telegram_edit_message_url == (
            "https://api.telegram.org/botbot-token/editMessageText"
        )
        assert settings.monitor_telegram_api_url == (
            "https://api.telegram.org/botbot-token/sendMessage"
        )


def test_load_settings_reads_project_dotenv_explicitly(monkeypatch):
    calls = []
    monkeypatch.setattr(config, "load_dotenv", lambda **kwargs: calls.append(kwargs))

    config.load_settings()

    assert calls == [{"dotenv_path": config.PROJECT_DIR / ".env"}]


def test_load_leagues_reads_and_validates_json(tmp_path):
    leagues_file = tmp_path / "leagues.json"
    leagues_file.write_text(json.dumps(["listed", "another"]), encoding="utf-8")

    assert config.load_leagues(leagues_file) == ["listed", "another"]


def test_load_leagues_rejects_non_string_entries(tmp_path):
    leagues_file = tmp_path / "leagues.json"
    leagues_file.write_text(json.dumps(["listed", 1]), encoding="utf-8")

    with pytest.raises(ValueError, match="list of league names"):
        config.load_leagues(leagues_file)


def test_application_settings_are_centralized():
    assert config.FINISHED_TIME_MARKERS == ("90", "90+", "FT", "ET", "Penalty")
    assert config.ERROR_SCREENSHOTS_DIR == config.PROJECT_DIR / "logs" / "screenshots"
    assert config.BOT_LOG_FILE == config.PROJECT_DIR / "bot.log"
