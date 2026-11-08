"""Tests del arranque de la aplicación y del endpoint de salud."""

from collections.abc import Iterator
from unittest.mock import MagicMock

from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError
from sqlmodel import Session

from app.database import get_session
from app.main import app


def test_health_reports_ok(client: TestClient) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok"}


def test_health_reports_unavailable_database() -> None:
    broken_session = MagicMock(spec=Session)
    broken_session.connection.side_effect = OperationalError("SELECT 1", {}, Exception("caída"))

    def _broken() -> Iterator[Session]:
        yield broken_session

    app.dependency_overrides[get_session] = _broken
    try:
        with TestClient(app) as client:
            response = client.get("/health")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 503
    assert response.json() == {"status": "degraded", "database": "unavailable"}


def test_openapi_exposes_api_version_not_software_version(client: TestClient) -> None:
    info = client.get("/openapi.json").json()["info"]

    assert info["title"] == "Pokédex API"
    assert info["version"] == "v1"


def test_interactive_docs_are_available(client: TestClient) -> None:
    assert client.get("/docs").status_code == 200


def test_get_session_yields_a_working_session() -> None:
    generator = get_session()
    session = next(generator)
    try:
        assert isinstance(session, Session)
    finally:
        generator.close()
