"""Fixtures compartidas por toda la suite de tests.

Cada test usa una base de datos SQLite **en memoria** recién creada, así que los tests
son independientes entre sí, no dejan ficheros y no tocan la base de datos real.
"""

import os

# La configuración se valida al importar la aplicación, así que el entorno de pruebas
# debe estar definido antes de cualquier import de ``app``.
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("SECRET_KEY", "clave-solo-para-tests-" + "x" * 32)
os.environ.setdefault("DATABASE_URL", "sqlite://")
# Coste mínimo de bcrypt en los tests: el algoritmo es el mismo, pero cada hash tarda
# milisegundos en lugar de ~250 ms.
os.environ.setdefault("BCRYPT_ROUNDS", "4")

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel

from app import models as _models  # noqa: F401  (registra las tablas en los metadatos)
from app.database import build_engine, get_session
from app.main import app


@pytest.fixture
def engine() -> Iterator[Engine]:
    # StaticPool reutiliza una única conexión: imprescindible con SQLite en memoria,
    # donde cada conexión nueva vería una base de datos vacía distinta.
    test_engine = build_engine("sqlite://", poolclass=StaticPool)
    SQLModel.metadata.create_all(test_engine)
    yield test_engine
    SQLModel.metadata.drop_all(test_engine)
    test_engine.dispose()


@pytest.fixture
def session(engine: Engine) -> Iterator[Session]:
    with Session(engine) as db_session:
        yield db_session


@pytest.fixture
def client(engine: Engine) -> Iterator[TestClient]:
    def _get_test_session() -> Iterator[Session]:
        with Session(engine) as db_session:
            yield db_session

    app.dependency_overrides[get_session] = _get_test_session
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


# ─────────────────────────────────────────────────────────────
#  Usuarios y autenticación
# ─────────────────────────────────────────────────────────────
TEST_PASSWORD = "Pikachu123"


@pytest.fixture
def test_user(client: TestClient) -> dict:
    """Usuario registrado a través de la API, con su contraseña en claro para los tests."""
    payload = {"username": "ash", "email": "ash@kanto.dev", "password": TEST_PASSWORD}
    response = client.post("/api/v1/auth/register", json=payload)
    assert response.status_code == 201, response.text
    return response.json() | {"password": TEST_PASSWORD}


@pytest.fixture
def tokens(client: TestClient, test_user: dict) -> dict:
    response = client.post(
        "/api/v1/auth/login",
        data={"username": test_user["username"], "password": test_user["password"]},
    )
    assert response.status_code == 200, response.text
    return response.json()


@pytest.fixture
def auth_headers(tokens: dict) -> dict[str, str]:
    return {"Authorization": f"Bearer {tokens['access_token']}"}
