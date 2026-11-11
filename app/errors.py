"""Traducción de las excepciones de dominio a respuestas HTTP.

Los servicios lanzan excepciones propias (no saben nada de HTTP) y aquí se decide qué
código y qué mensaje recibe el cliente. Los mensajes no incluyen detalles internos.
"""

import logging

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.config import get_settings
from app.services.pokeapi_service import (
    InvalidIdentifierError,
    PokeAPIError,
    PokeAPIUnavailableError,
    PokemonNotFoundError,
)

logger = logging.getLogger("pokedex_api.errors")


async def _not_found(_: Request, exc: PokemonNotFoundError) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_404_NOT_FOUND,
        content={"detail": f"{exc.resource} no encontrado: {exc.identifier}"},
    )


async def _invalid(_: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, content={"detail": str(exc)}
    )


async def _unavailable(request: Request, _: Exception) -> JSONResponse:
    logger.warning("PokeAPI no disponible al atender %s %s", request.method, request.url.path)
    retry_after = int(get_settings().pokeapi_breaker_cooldown_seconds)
    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content={
            "detail": "El servicio de datos de Pokémon no está disponible. Inténtalo más tarde."
        },
        headers={"Retry-After": str(retry_after)},
    )


async def _bad_gateway(request: Request, exc: Exception) -> JSONResponse:
    logger.error("Respuesta inesperada de PokeAPI en %s: %s", request.url.path, exc)
    return JSONResponse(
        status_code=status.HTTP_502_BAD_GATEWAY,
        content={"detail": "Respuesta inesperada del servicio de datos de Pokémon."},
    )


async def _validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    """422 de validación sin repetir lo que envió el cliente.

    FastAPI incluye por defecto el valor recibido (``input``) en cada error. Si lo que
    falla es la contraseña, la respuesta la repetiría, y podría acabar en logs de proxies o
    en herramientas de depuración. Aquí solo se devuelve qué campo falla y por qué.
    """
    errors = [
        {"type": e.get("type"), "loc": list(e.get("loc", ())), "msg": e.get("msg")}
        for e in exc.errors()
    ]
    logger.warning(
        "Error de validación en %s %s: %s",
        request.method,
        request.url.path,
        "; ".join(f"{'.'.join(str(p) for p in err['loc'])} ({err['type']})" for err in errors),
    )
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, content={"detail": errors}
    )


def register_exception_handlers(app: FastAPI) -> None:
    # Starlette elige el manejador de la clase más específica, así que el orden de
    # registro no importa: PokeAPIError solo captura lo que no encaja en las demás.
    app.add_exception_handler(RequestValidationError, _validation_error)
    app.add_exception_handler(PokemonNotFoundError, _not_found)
    app.add_exception_handler(InvalidIdentifierError, _invalid)
    app.add_exception_handler(PokeAPIUnavailableError, _unavailable)
    app.add_exception_handler(PokeAPIError, _bad_gateway)
