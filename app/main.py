"""Punto de entrada de la aplicación FastAPI.

La aplicación se construye con una *factory* (``create_app``) para que los tests puedan
crear instancias aisladas, y el arranque (creación de tablas) se hace en ``lifespan``,
que sustituye a los antiguos eventos ``startup`` y ``shutdown``.
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, FastAPI, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from slowapi.errors import RateLimitExceeded
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session

from app import docs
from app.config import get_settings
from app.database import create_db_and_tables, get_session
from app.errors import register_exception_handlers
from app.logging_config import configure_logging
from app.middleware import RequestLoggingMiddleware
from app.rate_limit import limiter, rate_limit_exceeded_handler
from app.routers import auth, battles, oak, oauth, pokedex, pokemon, pvp, teams, v2
from app.security_middleware import BodySizeLimitMiddleware, SecurityHeadersMiddleware
from app.services.ai_oak import OakAI
from app.services.google_oauth import GoogleOAuthClient
from app.services.pokeapi_service import PokeAPIService

logger = logging.getLogger("pokedex_api")

APP_DIR = Path(__file__).resolve().parent
STATIC_DIR = APP_DIR / "static"
WEB_DIR = APP_DIR / "web"
FONTS_DIR = APP_DIR / "assets" / "fonts"

API_DESCRIPTION = """
API REST de **Pokédex personal**: busca Pokémon en PokeAPI, guárdalos en tu colección,
márcalos como capturados, consulta tus estadísticas y monta equipos de hasta seis.

¿Prefieres usarla sin tecnicismos? Abre la [**web de la Pokédex**](/).

### Guía rápida

1. **Crea tu cuenta** en `POST /api/v1/auth/register` (*Try it out* → *Execute*).
2. Pulsa **Authorize** (arriba a la derecha) y escribe tu usuario y contraseña.
3. Prueba cualquier endpoint. Para empezar: busca `pikachu` en `GET /api/v1/pokemon/search`
   y añádelo con `POST /api/v1/pokedex` usando su `pokemon_id` (25).
"""

OPENAPI_TAGS = [
    {"name": "Autenticación", "description": "Registro, inicio de sesión y tokens."},
    {"name": "Pokémon", "description": "Catálogo de Pokémon (datos de PokeAPI)."},
    {"name": "Pokédex", "description": "Tu colección personal: añadir, capturar, exportar."},
    {"name": "Equipos", "description": "Equipos de hasta seis Pokémon y su análisis."},
    {"name": "Combates", "description": "Combates por turnos, experiencia, niveles y evoluciones."},
    {"name": "Profesor Oak (IA)", "description": "Consejos y búsqueda en lenguaje natural."},
    {"name": "v2", "description": "Versión 2: evoluciones y paginación por cursor."},
]


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
    app.state.google = GoogleOAuthClient()
    app.state.oak = OakAI()
    # No se registran versiones, rutas del sistema ni secretos: solo el entorno.
    logger.info("Aplicación iniciada (entorno: %s)", settings.environment)
    try:
        yield
    finally:
        await app.state.pokeapi.close()
        await app.state.google.close()
        await app.state.oak.close()
        logger.info("Aplicación detenida")


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings)

    app = FastAPI(
        title=settings.app_name,
        description=API_DESCRIPTION,
        openapi_tags=OPENAPI_TAGS,
        # Versión del contrato de la API, no del software: no se publica ninguna
        # versión de librerías ni del servidor.
        version="v1",
        lifespan=lifespan,
        # La página /docs es propia (app/docs.py): sin pistas de la tecnología y con los
        # recursos del CDN fijados por versión y verificados con SRI. ReDoc no se publica.
        docs_url=None,
        redoc_url=None,
        openapi_url="/openapi.json" if settings.docs_enabled else None,
    )
    if settings.docs_enabled:
        app.include_router(docs.router)
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    if settings.web_enabled:
        # Web de la Pokédex: HTML, CSS y JavaScript propios, sin compilación. Usa la
        # misma API que cualquier otro cliente, con su propia CSP (security_middleware).
        app.mount("/web", StaticFiles(directory=WEB_DIR), name="web")
        app.mount("/fonts", StaticFiles(directory=FONTS_DIR), name="fonts")

        @app.get("/", include_in_schema=False)
        def web_index() -> FileResponse:
            return FileResponse(WEB_DIR / "index.html", media_type="text/html; charset=utf-8")

    @app.get(
        "/health",
        response_model=HealthStatus,
        tags=["Sistema"],
        summary="Estado del servicio",
        # Es para los balanceadores y el healthcheck de Docker, no para los usuarios.
        include_in_schema=False,
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
    # Cadena de middlewares. El último que se añade es el más externo, así que una
    # petición los atraviesa en este orden:
    #
    #   RequestLogging → SecurityHeaders → TrustedHost → BodySizeLimit → CORS → rutas
    #
    # - El registro envuelve todo: mide la duración completa y ve todos los rechazos.
    # - Las cabeceras de seguridad se añaden también a los 400/413 de los siguientes.
    # - Un Host falso o un cuerpo enorme se rechazan antes de llegar a la aplicación.
    app.add_middleware(BodySizeLimitMiddleware, max_bytes=settings.max_body_bytes)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.allowed_hosts)
    app.add_middleware(SecurityHeadersMiddleware, hsts_max_age=settings.hsts_max_age_seconds)
    app.add_middleware(RequestLoggingMiddleware)

    api_v1 = APIRouter(prefix="/api/v1")
    api_v1.include_router(auth.router)
    api_v1.include_router(oauth.router)
    # Antes que el router de Pokémon: /pokemon/ask no debe leerse como /pokemon/{id_or_name}
    api_v1.include_router(oak.router)
    api_v1.include_router(pokemon.router)
    api_v1.include_router(pokedex.router)
    api_v1.include_router(teams.router)
    api_v1.include_router(battles.router)
    api_v1.include_router(pvp.router)
    app.include_router(api_v1)

    # v2: solo los recursos que han cambiado de forma incompatible. El resto sigue en v1.
    app.include_router(v2.router, prefix="/api/v2")

    return app


app = create_app()
