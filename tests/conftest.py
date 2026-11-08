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
