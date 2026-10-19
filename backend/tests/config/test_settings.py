"""Environment handling in config/settings.py.

The settings file is loaded fresh from its path for each case, under a throwaway module
name, so these tests never touch the settings object Django itself is using.
"""

import importlib.util
from pathlib import Path

import pytest
from django.core.exceptions import ImproperlyConfigured

SETTINGS_PATH = Path(__file__).parent.parent.parent / "config" / "settings.py"
ENV_VARS = (
    "DJANGO_SECRET_KEY",
    "DJANGO_DEBUG",
    "DJANGO_ALLOWED_HOSTS",
    "RENDER_EXTERNAL_HOSTNAME",
    "TRUCK_TIME_FACTOR",
    "HERE_API_KEY",
    "OPEN_METEO_BASE_URL",
    "OPEN_METEO_PROXY_TOKEN",
)


def load_settings(monkeypatch, **env):
    for name in ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    spec = importlib.util.spec_from_file_location("settings_under_test", SETTINGS_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_missing_secret_key_without_debug_fails_loudly(monkeypatch):
    with pytest.raises(ImproperlyConfigured, match="DJANGO_SECRET_KEY"):
        load_settings(monkeypatch)


def test_empty_secret_key_without_debug_fails(monkeypatch):
    with pytest.raises(ImproperlyConfigured):
        load_settings(monkeypatch, DJANGO_SECRET_KEY="")


def test_secret_key_comes_from_environment(monkeypatch):
    s = load_settings(monkeypatch, DJANGO_SECRET_KEY="from-the-environment")
    assert s.SECRET_KEY == "from-the-environment"
    assert s.DEBUG is False


def test_debug_without_secret_key_uses_dev_key_and_localhost(monkeypatch):
    s = load_settings(monkeypatch, DJANGO_DEBUG="true")
    assert s.DEBUG is True
    assert s.SECRET_KEY == "insecure-dev-only-key"
    assert "localhost" in s.ALLOWED_HOSTS
    assert "127.0.0.1" in s.ALLOWED_HOSTS


def test_environment_secret_key_wins_over_dev_key_in_debug(monkeypatch):
    s = load_settings(monkeypatch, DJANGO_DEBUG="true", DJANGO_SECRET_KEY="real")
    assert s.SECRET_KEY == "real"


@pytest.mark.parametrize("value", ["true", "True", "1", "yes", "on", " TRUE "])
def test_debug_truthy_values(monkeypatch, value):
    assert load_settings(monkeypatch, DJANGO_DEBUG=value, DJANGO_SECRET_KEY="k").DEBUG is True


@pytest.mark.parametrize("value", ["", "false", "0", "no", "off", "maybe", "tru"])
def test_debug_is_off_unless_explicitly_truthy(monkeypatch, value):
    assert load_settings(monkeypatch, DJANGO_DEBUG=value, DJANGO_SECRET_KEY="k").DEBUG is False


def test_debug_defaults_to_off(monkeypatch):
    assert load_settings(monkeypatch, DJANGO_SECRET_KEY="k").DEBUG is False


def test_allowed_hosts_parsed_and_trimmed(monkeypatch):
    s = load_settings(
        monkeypatch,
        DJANGO_SECRET_KEY="k",
        DJANGO_ALLOWED_HOSTS=" app.onrender.com , example.com,, ",
    )
    assert s.ALLOWED_HOSTS == ["app.onrender.com", "example.com"]


def test_allowed_hosts_empty_in_production_by_default(monkeypatch):
    assert load_settings(monkeypatch, DJANGO_SECRET_KEY="k").ALLOWED_HOSTS == []


def test_explicit_allowed_hosts_are_kept_in_debug(monkeypatch):
    s = load_settings(monkeypatch, DJANGO_DEBUG="true", DJANGO_ALLOWED_HOSTS="only.example.com")
    assert s.ALLOWED_HOSTS == ["only.example.com"]


def test_render_external_hostname_is_added(monkeypatch):
    s = load_settings(
        monkeypatch,
        DJANGO_SECRET_KEY="k",
        RENDER_EXTERNAL_HOSTNAME="svc.onrender.com",
        DJANGO_ALLOWED_HOSTS="custom.example.com",
    )
    assert s.ALLOWED_HOSTS == ["custom.example.com", "svc.onrender.com"]


def test_render_external_hostname_is_not_duplicated(monkeypatch):
    s = load_settings(
        monkeypatch,
        DJANGO_SECRET_KEY="k",
        RENDER_EXTERNAL_HOSTNAME="svc.onrender.com",
        DJANGO_ALLOWED_HOSTS="svc.onrender.com",
    )
    assert s.ALLOWED_HOSTS == ["svc.onrender.com"]


def test_truck_time_factor_default_and_override(monkeypatch):
    assert load_settings(monkeypatch, DJANGO_SECRET_KEY="k").TRUCK_TIME_FACTOR == 1.0
    s = load_settings(monkeypatch, DJANGO_SECRET_KEY="k", TRUCK_TIME_FACTOR="1.25")
    assert s.TRUCK_TIME_FACTOR == 1.25


def test_open_meteo_defaults_and_optional_proxy_settings(monkeypatch):
    defaults = load_settings(monkeypatch, DJANGO_SECRET_KEY="k")
    assert defaults.OPEN_METEO_BASE_URL == "https://api.open-meteo.com"
    assert defaults.OPEN_METEO_PROXY_TOKEN is None

    configured = load_settings(
        monkeypatch,
        DJANGO_SECRET_KEY="k",
        OPEN_METEO_BASE_URL="https://weather-proxy.example/",
        OPEN_METEO_PROXY_TOKEN="proxy-token",
    )
    assert configured.OPEN_METEO_BASE_URL == "https://weather-proxy.example"
    assert configured.OPEN_METEO_PROXY_TOKEN == "proxy-token"


def test_here_api_key_defaults_and_override(monkeypatch):
    assert load_settings(monkeypatch, DJANGO_SECRET_KEY="k").HERE_API_KEY == ""
    s = load_settings(monkeypatch, DJANGO_SECRET_KEY="k", HERE_API_KEY="test-here-key")
    assert s.HERE_API_KEY == "test-here-key"


def test_repo_contains_no_real_secret_defaults():
    text = SETTINGS_PATH.read_text(encoding="utf-8")
    assert "pk." not in text
    assert "sk." not in text
