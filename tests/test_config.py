import importlib
from contextlib import contextmanager

import pytest

import config


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
