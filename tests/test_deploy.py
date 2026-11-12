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
    assert env["CLIENT_IP_HEADER"]["value"] == "cf-connecting-ip"
    for key in ("DATABASE_URL", "GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GROQ_API_KEY"):
        assert env[key] == {"key": key, "sync": False}


async def seen_by_the_app(headers: list[tuple[bytes, bytes]], kind: str = "http") -> tuple:
    from app.security_middleware import TrustedProxyMiddleware

    seen = {}

    async def app(scope, receive, send):
        seen.update(client=scope["client"][0], scheme=scope["scheme"])

    scope = {"type": kind, "client": ("10.1.2.3", 5000), "scheme": "http", "headers": headers}
    if kind == "websocket":
        scope["scheme"] = "ws"
    await TrustedProxyMiddleware(app, "CF-Connecting-IP")(scope, None, None)
    return seen["client"], seen["scheme"]


async def test_the_real_ip_comes_from_the_proxy_header() -> None:
    headers = [
        (b"x-forwarded-for", b"6.6.6.6, 203.0.113.9"),  # el primero lo escribe el cliente
        (b"cf-connecting-ip", b"203.0.113.9"),
        (b"x-forwarded-proto", b"https"),
    ]
    assert await seen_by_the_app(headers) == ("203.0.113.9", "https")
    assert await seen_by_the_app(headers, "websocket") == ("203.0.113.9", "wss")


@pytest.mark.parametrize(
    "headers",
    [
        [(b"x-forwarded-for", b"6.6.6.6"), (b"x-forwarded-proto", b"https")],  # sin la cabecera
        [(b"cf-connecting-ip", b"no-es-una-ip"), (b"x-forwarded-proto", b"https")],
    ],
)
async def test_without_a_valid_proxy_header_nothing_is_trusted(headers) -> None:
    assert await seen_by_the_app(headers) == ("10.1.2.3", "http")


def test_the_proxy_middleware_is_off_by_default() -> None:
    from app.main import app
    from app.security_middleware import TrustedProxyMiddleware

    assert all(m.cls is not TrustedProxyMiddleware for m in app.user_middleware)
