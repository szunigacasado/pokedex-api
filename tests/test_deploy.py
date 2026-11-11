"""Despliegue: dirección pública, PostgreSQL y el fichero de Render."""

from pathlib import Path

import pytest
import yaml

from app.config import Settings
from app.database import build_engine, normalize_url

ROOT = Path(__file__).resolve().parent.parent


def settings(**env: str) -> Settings:
    return Settings(_env_file=None, **env)  # type: ignore[arg-type]


def test_public_hostname_is_allowed_and_used_for_google(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RENDER_EXTERNAL_HOSTNAME", "pokedex-api-x1y2.onrender.com")
    monkeypatch.delenv("GOOGLE_REDIRECT_URI", raising=False)
    s = settings()
    assert "pokedex-api-x1y2.onrender.com" in s.allowed_hosts
    assert s.google_redirect_uri == (
        "https://pokedex-api-x1y2.onrender.com/api/v1/auth/google/callback"
    )


def test_an_explicit_google_redirect_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PUBLIC_HOSTNAME", "pokedex.example.com")
    s = settings(google_redirect_uri="https://otra.example.com/callback")
    assert s.google_redirect_uri == "https://otra.example.com/callback"
    assert s.allowed_hosts.count("pokedex.example.com") == 1


def test_without_public_hostname_nothing_changes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RENDER_EXTERNAL_HOSTNAME", raising=False)
    monkeypatch.delenv("PUBLIC_HOSTNAME", raising=False)
    monkeypatch.delenv("GOOGLE_REDIRECT_URI", raising=False)
    s = settings(allowed_hosts="localhost")
    assert s.allowed_hosts == ["localhost"]
    assert s.google_redirect_uri.startswith("http://localhost")


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("postgres://u:p@h/db?sslmode=require", "postgresql+psycopg://u:p@h/db?sslmode=require"),
        ("postgresql://u:p@h/db", "postgresql+psycopg://u:p@h/db"),
        ("postgresql+psycopg://u:p@h/db", "postgresql+psycopg://u:p@h/db"),
        ("sqlite:///./pokedex.db", "sqlite:///./pokedex.db"),
    ],
)
def test_postgres_urls_use_psycopg(url: str, expected: str) -> None:
    assert normalize_url(url) == expected


def test_postgres_engine_checks_connections_before_use() -> None:
    engine = build_engine("postgres://u:p@localhost/db")
    assert engine.dialect.driver == "psycopg"
    assert engine.pool._pre_ping
    engine.dispose()


def test_render_only_deploys_main_after_the_ci() -> None:
    blueprint = yaml.safe_load((ROOT / "render.yaml").read_text(encoding="utf-8"))
    (service,) = blueprint["services"]
    assert service["branch"] == "main"
    assert service["autoDeployTrigger"] == "checksPass"
    assert service["runtime"] == "docker"
    env = {v["key"]: v for v in service["envVars"]}
    assert env["ENVIRONMENT"]["value"] == "production"
    assert env["SECRET_KEY"] == {"key": "SECRET_KEY", "generateValue": True}
    # Ningún secreto escrito en el fichero
    for key in ("DATABASE_URL", "GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GROQ_API_KEY"):
        assert env[key] == {"key": key, "sync": False}
