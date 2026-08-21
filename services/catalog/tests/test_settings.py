"""Configuration has to survive the environment it is actually given.

platform/.env writes trailing comments on the same line as a value, and
Docker Compose passes some of them into the container verbatim. A setting
that cannot cope with that takes the service down at import time — which is
exactly what a malformed `SENTRY_DSN` did before this guard existed.
"""

from __future__ import annotations

import importlib

import pytest

settings_module = importlib.import_module("catalog.settings")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("json", "json"),
        ("json             # structured JSON to stdout, never print()", "json"),
        ("  json  ", "json"),
        ("", "plain-default"),
    ],
)
def test_a_single_word_setting_ignores_the_prose_after_it(
    monkeypatch: pytest.MonkeyPatch, raw: str, expected: str
) -> None:
    monkeypatch.setenv("CATALOG_PROBE", raw)
    assert settings_module._env_word("CATALOG_PROBE", "plain-default") == expected


def test_numeric_settings_survive_a_trailing_comment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CATALOG_PROBE_INT", "60   # seconds")
    monkeypatch.setenv("CATALOG_PROBE_FLOAT", "0.05 # sample rate")
    assert settings_module._env_int("CATALOG_PROBE_INT", 1) == 60
    assert settings_module._env_float("CATALOG_PROBE_FLOAT", 1.0) == 0.05


def test_an_empty_sentry_dsn_disables_sentry(monkeypatch: pytest.MonkeyPatch) -> None:
    """`SENTRY_DSN=   # empty disables Sentry` must mean disabled, not crash."""
    monkeypatch.setenv("SENTRY_DSN", "                 # empty disables Sentry")
    assert "://" not in settings_module._env_word("SENTRY_DSN")


def test_the_redis_database_comes_from_the_environment() -> None:
    """SPEC.md says Redis db 5; platform/.env assigns catalog db 4 and db 5
    to search. Taking the URL from the environment keeps this service right
    either way, and stops it from sharing a database with search."""
    from_environment = settings_module._env("REDIS_URL", "redis://redis:6379/4")
    assert from_environment == settings_module.REDIS_URL
    # Never db 5: that one belongs to search.
    assert not settings_module.REDIS_URL.endswith("/5")
