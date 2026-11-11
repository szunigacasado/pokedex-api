"""Tests de rate limiting y CORS."""

import logging

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.config import Settings, get_settings

LOGIN = "/api/v1/auth/login"
REGISTER = "/api/v1/auth/register"
ME = "/api/v1/auth/me"
KEY = "k" * 32


# ═════════════════════════════════════════════════════════════
#  Rate limiting
# ═════════════════════════════════════════════════════════════
def test_rate_limit_exceeded(client: TestClient) -> None:
    """Test del enunciado: la 11.ª petición de login en un minuto devuelve 429.

    Se usa un usuario inexistente para que el 429 sea del rate limit por IP y no del
    bloqueo de la cuenta por intentos fallidos.
    """
    for _ in range(10):
        response = client.post(LOGIN, data={"username": "nadie", "password": "Mal12345"})
        assert response.status_code == 401

    response = client.post(LOGIN, data={"username": "nadie", "password": "Mal12345"})

    assert response.status_code == 429
    assert response.json()["limit"] == "10 per 1 minute"
    assert int(response.headers["retry-after"]) > 0
    assert response.headers["x-ratelimit-remaining"] == "0"


def test_registration_is_limited_per_hour(client: TestClient) -> None:
    for i in range(5):
        body = {"username": f"user{i}", "email": f"u{i}@kanto.dev", "password": "Pikachu123"}
        assert client.post(REGISTER, json=body).status_code == 201

    body = {"username": "user6", "email": "u6@kanto.dev", "password": "Pikachu123"}
    response = client.post(REGISTER, json=body)

    assert response.status_code == 429
    assert response.json()["limit"] == "5 per 1 hour"


def test_successful_responses_carry_rate_limit_headers(client: TestClient, test_user) -> None:
    response = client.post(LOGIN, data={"username": "ash", "password": test_user["password"]})

    assert response.status_code == 200
    assert response.headers["x-ratelimit-limit"] == "10"
    assert response.headers["x-ratelimit-remaining"] == "9"
    assert "x-ratelimit-reset" in response.headers


def test_authenticated_limits_are_per_user(
    client: TestClient, auth_headers: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Dos usuarios desde la misma IP: que uno agote su cupo no afecta al otro."""
    monkeypatch.setattr(get_settings(), "rate_limit_default", "3/minute")
    client.post(
        REGISTER, json={"username": "gary", "email": "gary@kanto.dev", "password": "Eevee1234"}
    )
    gary = client.post(LOGIN, data={"username": "gary", "password": "Eevee1234"}).json()
    gary_headers = {"Authorization": f"Bearer {gary['access_token']}"}

    for _ in range(3):
        assert client.get(ME, headers=auth_headers).status_code == 200
    assert client.get(ME, headers=auth_headers).status_code == 429

    assert client.get(ME, headers=gary_headers).status_code == 200


def test_limits_are_read_from_configuration(
    client: TestClient, auth_headers: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "rate_limit_default", "1/minute")

    assert client.get(ME, headers=auth_headers).headers["x-ratelimit-limit"] == "1"


def test_rate_limit_hits_are_logged(client: TestClient, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger="pokedex_api.rate_limit"):
        for _ in range(11):
            client.post(LOGIN, data={"username": "nadie", "password": "Mal12345"})

    assert "Rate limit superado: POST /api/v1/auth/login clave=ip:testclient" in caplog.text


def test_health_is_not_rate_limited(client: TestClient) -> None:
    for _ in range(120):
        assert client.get("/health").status_code == 200


# ═════════════════════════════════════════════════════════════
#  Configuración
# ═════════════════════════════════════════════════════════════
def make_settings(**overrides) -> Settings:
    return Settings(_env_file=None, secret_key=KEY, **overrides)  # type: ignore[arg-type]


def test_invalid_rate_limit_is_rejected_at_startup() -> None:
    with pytest.raises(ValidationError, match="RATE_LIMIT_LOGIN"):
        make_settings(rate_limit_login="diez por minuto")


def test_cors_origins_from_comma_separated_text() -> None:
    settings = make_settings(cors_origins="https://pokedex.app/ , http://localhost:5173")

    assert settings.cors_origins == ["https://pokedex.app", "http://localhost:5173"]


def test_cors_origins_from_json() -> None:
    settings = make_settings(cors_origins='["https://pokedex.app"]')

    assert settings.cors_origins == ["https://pokedex.app"]


def test_cors_wildcard_is_forbidden_in_production() -> None:
    with pytest.raises(ValidationError, match="CORS_ORIGINS"):
        make_settings(cors_origins="*", environment="production")

    assert make_settings(cors_origins="*").cors_origins == ["*"]


def test_cors_origin_needs_a_scheme() -> None:
    with pytest.raises(ValidationError, match="Origen CORS no válido"):
        make_settings(cors_origins="pokedex.app")


# ═════════════════════════════════════════════════════════════
#  CORS
# ═════════════════════════════════════════════════════════════
def preflight(client: TestClient, origin: str):
    return client.options(
        ME,
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "Authorization",
        },
    )


def test_preflight_from_allowed_origin(client: TestClient) -> None:
    response = preflight(client, "http://localhost:5173")

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"
    assert response.headers["access-control-max-age"] == "3600"
    assert "access-control-allow-credentials" not in response.headers
    assert "authorization" in response.headers["access-control-allow-headers"].lower()


def test_preflight_from_unknown_origin_is_rejected(client: TestClient) -> None:
    response = preflight(client, "https://web-maliciosa.example")

    assert response.status_code == 400
    assert "access-control-allow-origin" not in response.headers


def test_disallowed_method_is_rejected(client: TestClient) -> None:
    response = client.options(
        ME, headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "TRACE"}
    )

    assert response.status_code == 400


def test_browser_can_read_exposed_headers(client: TestClient, test_user) -> None:
    response = client.post(
        LOGIN,
        data={"username": "ash", "password": test_user["password"]},
        headers={"Origin": "http://localhost:3000"},
    )

    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"
    exposed = response.headers["access-control-expose-headers"].lower()
    assert "x-ratelimit-remaining" in exposed
    assert "content-disposition" in exposed
