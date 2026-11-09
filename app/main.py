"""Punto de entrada de la aplicación FastAPI.

La aplicación se construye con una *factory* (``create_app``) para que los tests puedan
crear instancias aisladas, y el arranque (creación de tablas) se hace en ``lifespan``,
que sustituye a los antiguos eventos ``startup`` y ``shutdown``.
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import APIRouter, Depends, FastAPI, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from slowapi.errors import RateLimitExceeded
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session

from app.config import get_settings
from app.database import create_db_and_tables, get_session
from app.errors import register_exception_handlers
from app.logging_config import configure_logging
from app.middleware import RequestLoggingMiddleware
from app.rate_limit import limiter, rate_limit_exceeded_handler
from app.routers import auth, pokedex, pokemon, teams
from app.services.pokeapi_service import PokeAPIService

logger = logging.getLogger("pokedex_api")

API_DESCRIPTION = """
API REST de **Pokédex personal**.

Busca Pokémon en la PokeAPI pública, guárdalos en tu colección, márcalos como
capturados, consulta tus estadísticas y monta equipos de batalla de hasta seis Pokémon.
"""


class HealthStatus(BaseModel):
    status: str
    database: str


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    create_db_and_tables()
    logger.info("Base de datos inicializada")

    # Un único cliente HTTP para toda la aplicación: reutiliza conexiones (keep-alive)
    # y comparte caché y circuit breaker entre peticiones.
    app.state.pokeapi = PokeAPIService()
    # No se registran versiones, rutas del sistema ni secretos: solo el entorno.
    logger.info("Aplicación iniciada (entorno: %s)", settings.environment)
    try:
        yield
    finally:
        await app.state.pokeapi.close()
        logger.info("Aplicación detenida")


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings)

    app = FastAPI(
        title=settings.app_name,
        description=API_DESCRIPTION,
        # Versión del contrato de la API, no del software: no se publica ninguna
        # versión de librerías ni del servidor.
        version="v1",
        lifespan=lifespan,
    )

    @app.get(
        "/health",
        response_model=HealthStatus,
        tags=["Sistema"],
        summary="Estado del servicio",
        responses={503: {"model": HealthStatus, "description": "Base de datos no disponible"}},
    )
    def health(session: Annotated[Session, Depends(get_session)]) -> HealthStatus | JSONResponse:
        """Comprueba que la API responde y que la base de datos acepta consultas."""
        try:
            session.connection().execute(text("SELECT 1"))
        except SQLAlchemyError:
            logger.exception("La comprobación de salud de la base de datos ha fallado")
            return JSONResponse(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                content=HealthStatus(status="degraded", database="unavailable").model_dump(),
            )
        return HealthStatus(status="ok", database="ok")

    register_exception_handlers(app)

    # Rate limiting (límites por endpoint en app/rate_limit.py)
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)

    # CORS: solo los orígenes configurados pueden llamar a la API desde un navegador.
    # allow_credentials=False porque la autenticación va en la cabecera Authorization,
    # no en cookies: no hace falta que el navegador envíe credenciales a otros orígenes.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Authorization", "Content-Type"],
        expose_headers=[
            "Content-Disposition",
            "Retry-After",
            "X-RateLimit-Limit",
            "X-RateLimit-Remaining",
            "X-RateLimit-Reset",
        ],
        max_age=settings.cors_max_age_seconds,
    )
    # El último middleware añadido es el más externo: el registro de peticiones envuelve
    # a todos los demás, así que mide la duración completa y ve también los rechazos de
    # CORS y los errores no controlados.
    app.add_middleware(RequestLoggingMiddleware)

    api_v1 = APIRouter(prefix="/api/v1")
    api_v1.include_router(auth.router)
    api_v1.include_router(pokemon.router)
    api_v1.include_router(pokedex.router)
    api_v1.include_router(teams.router)
    app.include_router(api_v1)

    return app


app = create_app()
