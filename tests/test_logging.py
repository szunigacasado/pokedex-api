"""Tests del logging: registro de peticiones, ID de petición, redacción de secretos,
errores de validación y errores no controlados."""

import json
import logging
import re
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient

from app.config import get_settings
from app.logging_config import APP_LOGGER, configure_logging, redact
from app.main import create_app

REQUEST_ID = re.compile(r"^[0-9a-f]{32}$")


def http_records(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.name == "pokedex_api.http"]


@pytest.fixture
def log_file(tmp_path: Path) -> Iterator[Path]:
    """Redirige temporalmente los logs de la aplicación a un fichero."""
    path = tmp_path / "pokedex_api.log"

    def _configure(fmt: str = "text") -> Path:
        configure_logging(
            get_settings().model_copy(update={"log_file": str(path), "log_format": fmt})
        )
        return path

    yield _configure
    configure_logging(get_settings())


# ═════════════════════════════════════════════════════════════
#  Registro de peticiones
# ═════════════════════════════════════════════════════════════
def test_every_request_is_logged(client: TestClient, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger=APP_LOGGER):
        client.get("/health")

    request, response = http_records(caplog)
    assert request.getMessage() == "Request: GET /health"
    assert re.fullmatch(r"Response: 200 \| Duration: \d+\.\d{3}s", response.getMessage())
    assert response.method == "GET"
    assert response.path == "/health"
    assert response.status_code == 200
    assert response.duration_ms >= 0
    assert response.client_ip == "testclient"


def test_authenticated_requests_log_the_user(
    client: TestClient, auth_headers: dict, test_user: dict, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO, logger=APP_LOGGER):
        client.get("/api/v1/auth/me", headers=auth_headers)

    assert http_records(caplog)[-1].user_id == test_user["id"]


def test_log_level_depends_on_the_status(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO, logger=APP_LOGGER):
        client.get("/no-existe")

    assert http_records(caplog)[-1].levelno == logging.WARNING


def test_sensitive_query_parameters_are_hidden(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO, logger=APP_LOGGER):
        client.get("/health", params={"token": "supersecreto", "page": "2"})

    request = http_records(caplog)[0]
    assert "supersecreto" not in request.query
    assert "page=2" in request.query


# ═════════════════════════════════════════════════════════════
#  ID de petición
# ═════════════════════════════════════════════════════════════
def test_responses_carry_a_request_id(client: TestClient) -> None:
    assert REQUEST_ID.fullmatch(client.get("/health").headers["x-request-id"])


def test_valid_incoming_request_id_is_reused(client: TestClient) -> None:
    response = client.get("/health", headers={"X-Request-ID": "proxy-abc-12345"})

    assert response.headers["x-request-id"] == "proxy-abc-12345"


@pytest.mark.parametrize("bad", ["corto", "x" * 65, "<script>alert(1)</script>", "a b c d e f g h"])
def test_suspicious_request_ids_are_replaced(client: TestClient, bad: str) -> None:
    response = client.get("/health", headers={"X-Request-ID": bad})

    assert REQUEST_ID.fullmatch(response.headers["x-request-id"])


def test_log_lines_carry_the_request_id(client: TestClient, log_file) -> None:
    path = log_file("text")

    request_id = client.get("/health").headers["x-request-id"]

    lines = path.read_text(encoding="utf-8").splitlines()
    assert any(
        f"- pokedex_api.http - INFO - [{request_id}] Request: GET /health" in line for line in lines
    )
    assert any(f"[{request_id}] Response: 200 | Duration:" in line for line in lines)


def test_json_log_format(client: TestClient, log_file) -> None:
    path = log_file("json")

    request_id = client.get("/health").headers["x-request-id"]

    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    response = next(r for r in records if r["message"].startswith("Response"))
    assert response["request_id"] == request_id
    assert response["status_code"] == 200
    assert response["level"] == "INFO"
    assert response["logger"] == "pokedex_api.http"
    assert "timestamp" in response


def test_configuring_twice_does_not_duplicate_handlers() -> None:
    configure_logging(get_settings())
    configure_logging(get_settings())

    assert len(logging.getLogger(APP_LOGGER).handlers) == 1


# ═════════════════════════════════════════════════════════════
#  Redacción de secretos
# ═════════════════════════════════════════════════════════════
JWT = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJhc2gifQ.firma-secreta_123"


@pytest.mark.parametrize(
    ("text", "secret"),
    [
        (f"Authorization: Bearer {JWT}", JWT),
        (f"token suelto {JWT} en un mensaje", JWT),
        ("password=Pikachu123&user=ash", "Pikachu123"),
        ('{"password": "Pikachu123"}', "Pikachu123"),
        ("SECRET_KEY=mi-clave-super-secreta", "mi-clave-super-secreta"),
        ('"refresh_token": "abc.def.ghi"', "abc.def.ghi"),
    ],
)
def test_secrets_are_redacted(text: str, secret: str) -> None:
    cleaned = redact(text)

    assert secret not in cleaned
    assert "REDACTED" in cleaned


def test_redaction_is_applied_to_real_log_lines(log_file) -> None:
    path = log_file("text")

    logging.getLogger("pokedex_api.prueba").warning("Cabecera recibida: Bearer %s", JWT)

    content = path.read_text(encoding="utf-8")
    assert JWT not in content
    assert "Bearer [REDACTED]" in content


# ═════════════════════════════════════════════════════════════
#  Errores de validación
# ═════════════════════════════════════════════════════════════
def test_validation_errors_do_not_echo_the_input(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING, logger=APP_LOGGER):
        response = client.post(
            "/api/v1/auth/register",
            json={"username": "ash", "email": "ash@kanto.dev", "password": "secreta"},
        )

    assert response.status_code == 422
    error = response.json()["detail"][0]
    assert set(error) == {"type", "loc", "msg"}
    assert error["loc"] == ["body", "password"]
    assert "secreta" not in response.text

    assert "Error de validación en POST /api/v1/auth/register: body.password" in caplog.text
    assert "secreta" not in caplog.text


# ═════════════════════════════════════════════════════════════
#  Errores no controlados
# ═════════════════════════════════════════════════════════════
@pytest.fixture
def broken_app() -> Iterator[TestClient]:
    app = create_app()

    @app.get("/explota")
    def explota() -> None:
        raise RuntimeError(f"fallo interno en /srv/app/db.py con Bearer {JWT}")

    @app.get("/explota-a-medias")
    def explota_a_medias() -> StreamingResponse:
        def chunks() -> Iterator[bytes]:
            yield b"primera parte"
            raise RuntimeError("fallo a mitad de la respuesta")

        return StreamingResponse(chunks())

    with TestClient(app) as client:
        yield client


def test_unhandled_errors_return_a_safe_500(
    broken_app: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO, logger=APP_LOGGER):
        response = broken_app.get("/explota")

    assert response.status_code == 500
    body = response.json()
    assert body["request_id"] == response.headers["x-request-id"]
    assert "Error interno" in body["detail"]
    for leak in ("RuntimeError", "/srv/app", "Traceback", JWT):
        assert leak not in response.text

    error = next(r for r in caplog.records if r.getMessage().startswith("Error no controlado"))
    assert error.levelno == logging.ERROR
    assert error.exc_info is not None
    assert http_records(caplog)[-1].status_code == 500


def test_errors_after_the_response_started_are_logged_and_reraised(
    broken_app: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.ERROR, logger=APP_LOGGER), pytest.raises(RuntimeError):
        broken_app.get("/explota-a-medias")

    assert "Error no controlado en GET /explota-a-medias" in caplog.text


def test_exception_traces_in_log_files_are_redacted(broken_app: TestClient, log_file) -> None:
    path = log_file("text")

    broken_app.get("/explota")

    content = path.read_text(encoding="utf-8")
    assert "Traceback" in content
    assert JWT not in content


def test_json_logs_include_redacted_exceptions(broken_app: TestClient, log_file) -> None:
    path = log_file("json")

    broken_app.get("/explota")

    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    error = next(r for r in records if r["message"].startswith("Error no controlado"))
    assert "RuntimeError" in error["exception"]
    assert JWT not in error["exception"]
