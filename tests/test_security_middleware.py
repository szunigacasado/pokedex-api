"""Tests de los middlewares de seguridad, de la página /docs propia y de la cadena."""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.config import Settings, get_settings
from app.docs import SWAGGER_CSS, SWAGGER_JS, SWAGGER_VERSION
from app.main import create_app
from app.security_middleware import API_CSP, DOCS_CSP

KEY = "k" * 32
SECURITY_HEADERS = {
    "x-content-type-options": "nosniff",
    "x-frame-options": "DENY",
    "referrer-policy": "no-referrer",
    "cross-origin-opener-policy": "same-origin",
    "cross-origin-resource-policy": "same-origin",
    "x-permitted-cross-domain-policies": "none",
}


# ═════════════════════════════════════════════════════════════
#  Cabeceras de seguridad
# ═════════════════════════════════════════════════════════════
@pytest.mark.parametrize(
    ("method", "path", "status"),
    [
        ("GET", "/health", 200),
        ("GET", "/api/v1/auth/me", 401),
        ("GET", "/no-existe", 404),
        ("POST", "/api/v1/auth/register", 422),
    ],
)
def test_every_response_has_security_headers(
    client: TestClient, method: str, path: str, status: int
) -> None:
    response = client.request(method, path, json={})

    assert response.status_code == status
    for header, value in SECURITY_HEADERS.items():
        assert response.headers[header] == value
    assert response.headers["content-security-policy"] == API_CSP
    assert "camera=()" in response.headers["permissions-policy"]
    assert response.headers["cache-control"] == "no-store"


def test_no_server_or_technology_is_revealed(client: TestClient) -> None:
    response = client.get("/health")

    assert "server" not in response.headers
    assert "x-powered-by" not in response.headers


def test_hsts_only_over_https(client: TestClient) -> None:
    assert "strict-transport-security" not in client.get("/health").headers

    https = TestClient(client.app, base_url="https://testserver")
    hsts = https.get("/health").headers["strict-transport-security"]

    assert hsts == "max-age=63072000; includeSubDomains"


def test_endpoints_can_keep_their_own_cache_policy(
    client: TestClient, auth_headers: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    """El middleware solo pone no-store si el endpoint no ha decidido otra cosa."""
    response = client.get("/static/favicon.svg")

    assert response.headers["cache-control"] == "public, max-age=86400"


# ═════════════════════════════════════════════════════════════
#  Documentación
# ═════════════════════════════════════════════════════════════
def test_docs_page_has_no_technology_hints(client: TestClient) -> None:
    response = client.get("/docs")

    assert response.status_code == 200
    html = response.text
    assert "tiangolo" not in html
    assert "fastapi" not in html.lower()
    assert '<link rel="icon" type="image/svg+xml" href="/static/favicon.svg">' in html


def test_docs_assets_are_pinned_and_verified_with_sri(client: TestClient) -> None:
    html = client.get("/docs").text

    for url, integrity in (SWAGGER_JS, SWAGGER_CSS):
        assert f"@{SWAGGER_VERSION}/" in url
        assert f'href="{url}" integrity="{integrity}"' in html or (
            f'src="{url}" integrity="{integrity}"' in html
        )
    assert "<script>" not in html  # ningún script en línea


def test_docs_have_their_own_csp(client: TestClient) -> None:
    csp = client.get("/docs").headers["content-security-policy"]

    assert csp == DOCS_CSP
    assert "script-src 'self' https://cdn.jsdelivr.net;" in csp
    assert "'unsafe-inline'" not in csp.split("style-src")[0]  # nunca para scripts


def test_static_assets_are_served(client: TestClient) -> None:
    assert client.get("/static/swagger-init.js").status_code == 200
    assert "<svg" in client.get("/static/favicon.svg").text


def test_redoc_is_not_published(client: TestClient) -> None:
    assert client.get("/redoc").status_code == 404


@pytest.fixture
def no_docs_client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setattr(get_settings(), "docs_enabled", False)
    with TestClient(create_app()) as client:
        yield client


def test_docs_can_be_disabled(no_docs_client: TestClient) -> None:
    assert no_docs_client.get("/docs").status_code == 404
    assert no_docs_client.get("/openapi.json").status_code == 404
    assert no_docs_client.get("/health").status_code == 200


# ═════════════════════════════════════════════════════════════
#  Límite de tamaño del cuerpo
# ═════════════════════════════════════════════════════════════
def test_declared_oversized_body_is_rejected(client: TestClient) -> None:
    payload = b"x" * (get_settings().max_body_bytes + 1)

    response = client.post(
        "/api/v1/auth/register", content=payload, headers={"Content-Type": "application/json"}
    )

    assert response.status_code == 413
    assert response.json()["detail"] == "La petición es demasiado grande (máximo 1024 KB)."
    assert response.headers["x-content-type-options"] == "nosniff"


def test_chunked_oversized_body_is_cut_while_streaming(client: TestClient) -> None:
    """Sin Content-Length, los bytes se cuentan según llegan."""
    limit = get_settings().max_body_bytes

    def chunks() -> Iterator[bytes]:
        for _ in range(limit // 65536 + 2):
            yield b"x" * 65536

    response = client.post(
        "/api/v1/auth/register", content=chunks(), headers={"Content-Type": "application/json"}
    )

    assert response.status_code == 413


def test_invalid_content_length_is_rejected(client: TestClient) -> None:
    response = client.post("/api/v1/auth/register", headers={"Content-Length": "mucho"})

    assert response.status_code == 400


def test_normal_bodies_pass(client: TestClient) -> None:
    body = {"username": "ash", "email": "ash@kanto.dev", "password": "Pikachu123"}

    assert client.post("/api/v1/auth/register", json=body).status_code == 201


# ═════════════════════════════════════════════════════════════
#  Validación del Host
# ═════════════════════════════════════════════════════════════
def test_unknown_host_is_rejected(client: TestClient) -> None:
    response = client.get("/health", headers={"Host": "evil.example"})

    assert response.status_code == 400
    assert response.headers["x-frame-options"] == "DENY"


def test_allowed_host_with_port_is_accepted(client: TestClient) -> None:
    assert client.get("/health", headers={"Host": "localhost:8000"}).status_code == 200


def test_allowed_hosts_from_text() -> None:
    settings = Settings(
        _env_file=None, secret_key=KEY, allowed_hosts="API.ejemplo.com, *.ejemplo.com"
    )

    assert settings.allowed_hosts == ["api.ejemplo.com", "*.ejemplo.com"]


def test_allowed_hosts_from_json() -> None:
    settings = Settings(_env_file=None, secret_key=KEY, allowed_hosts='["api.ejemplo.com"]')

    assert settings.allowed_hosts == ["api.ejemplo.com"]


def test_wildcard_host_is_forbidden_in_production() -> None:
    with pytest.raises(ValidationError, match="ALLOWED_HOSTS"):
        Settings(_env_file=None, secret_key=KEY, allowed_hosts="*", environment="production")
