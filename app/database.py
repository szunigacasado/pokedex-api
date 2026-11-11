"""Motor de base de datos y sesiones por petición."""

from collections.abc import Iterator
from typing import Any

from sqlalchemy import Engine, event
from sqlmodel import Session, SQLModel, create_engine

from app.config import get_settings


def normalize_url(database_url: str) -> str:
    """Los proveedores de PostgreSQL dan la cadena como ``postgres://`` o
    ``postgresql://``; SQLAlchemy necesita que se indique el driver (psycopg 3)."""
    for prefix in ("postgres://", "postgresql://"):
        if database_url.startswith(prefix):
            return "postgresql+psycopg://" + database_url.removeprefix(prefix)
    return database_url


def build_engine(database_url: str, **kwargs: Any) -> Engine:
    """Crea el motor de SQLAlchemy adaptado al tipo de base de datos.

    - En SQLite se activan las claves foráneas, que por defecto vienen desactivadas: sin
      ellas la base de datos aceptaría entradas de Pokédex de usuarios inexistentes.
    - En PostgreSQL se comprueba cada conexión antes de usarla (``pool_pre_ping``): un
      servidor gestionado puede cerrar las inactivas, y así no falla la primera petición.
    """
    database_url = normalize_url(database_url)
    is_sqlite = database_url.startswith("sqlite")
    connect_args = {"check_same_thread": False} if is_sqlite else {}
    if not is_sqlite:
        kwargs.setdefault("pool_pre_ping", True)
    engine = create_engine(database_url, connect_args=connect_args, **kwargs)

    if is_sqlite:
        event.listen(engine, "connect", _enable_sqlite_foreign_keys)
    return engine


def _enable_sqlite_foreign_keys(dbapi_connection: Any, _connection_record: Any) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


engine = build_engine(get_settings().database_url)


def create_db_and_tables() -> None:
    """Crea las tablas que falten a partir de los modelos registrados."""
    import app.models  # noqa: F401  (registra las tablas en los metadatos)

    SQLModel.metadata.create_all(engine)


def get_session() -> Iterator[Session]:
    """Dependencia de FastAPI: abre una sesión por petición y la cierra al terminar."""
    with Session(engine) as session:
        yield session
