"""Tests de la configuración: la aplicación debe negarse a arrancar con claves débiles."""

import pytest
from pydantic import ValidationError

from app.config import MIN_SECRET_KEY_LENGTH, Settings, get_settings

STRONG_KEY = "k" * MIN_SECRET_KEY_LENGTH
EXAMPLE_KEY = "cambia-esto-por-una-clave-aleatoria-de-64-caracteres"


def make_settings(**overrides: object) -> Settings:
    # _env_file=None aísla el test de cualquier .env local del desarrollador.
    return Settings(_env_file=None, **overrides)  # type: ignore[arg-type]


def test_short_secret_key_is_rejected() -> None:
    with pytest.raises(ValidationError, match="SECRET_KEY"):
        make_settings(secret_key="corta")


def test_example_secret_key_is_rejected_in_production() -> None:
    with pytest.raises(ValidationError, match=r"\.env\.example"):
        make_settings(secret_key=EXAMPLE_KEY, environment="production")


def test_example_secret_key_is_allowed_in_development() -> None:
    settings = make_settings(secret_key=EXAMPLE_KEY, environment="development")
    assert settings.environment == "development"


def test_secret_key_is_not_exposed_in_repr() -> None:
    settings = make_settings(secret_key=STRONG_KEY)
    assert STRONG_KEY not in repr(settings)


def test_environment_helpers() -> None:
    settings = make_settings(
        secret_key=STRONG_KEY,
        environment="production",
        database_url="postgresql://usuario@localhost/pokedex",
    )
    assert settings.is_production
    assert not settings.is_sqlite


def test_invalid_environment_is_rejected() -> None:
    with pytest.raises(ValidationError):
        make_settings(secret_key=STRONG_KEY, environment="staging")


def test_settings_are_cached() -> None:
    assert get_settings() is get_settings()
