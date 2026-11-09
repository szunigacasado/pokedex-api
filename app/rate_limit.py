"""Rate limiting con slowapi.

**¿A quién se limita?**

- En los endpoints públicos (registro, login, renovación de tokens), a cada **IP**: no hay
  otra forma de identificar al cliente.
- En los endpoints autenticados, a cada **usuario**. Así, si varias personas comparten IP
  (una red universitaria o una VPN, por ejemplo), una no agota el cupo de las demás, y un
  mismo usuario no puede saltarse su límite cambiando de IP.

La dependencia de autenticación guarda el ID del usuario en ``request.state`` antes de que
se compruebe el límite, porque FastAPI resuelve las dependencias antes de ejecutar el
endpoint. Un token falsificado no llega aquí: la autenticación lo rechaza antes con 401.

**¿Cuánto?** Los límites se leen de la configuración (variables ``RATE_LIMIT_*``) cada
vez que se aplican, así que se cambian sin tocar el código.
"""

import logging
from collections.abc import Callable

from fastapi import Request
from fastapi.responses import JSONResponse
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from app.config import get_settings

logger = logging.getLogger("pokedex_api.rate_limit")


def rate_limit_key(request: Request) -> str:
    user_id = getattr(request.state, "user_id", None)
    if user_id is not None:
        return f"user:{user_id}"
    return f"ip:{get_remote_address(request)}"


def _setting(name: str) -> Callable[[], str]:
    return lambda: getattr(get_settings(), name)


# Límites por tipo de endpoint (ver «Decisiones de diseño» en el README).
REGISTER = _setting("rate_limit_register")  # 5/hour: crear cuentas en masa
LOGIN = _setting("rate_limit_login")  # 10/minute: fuerza bruta de contraseñas
TOKEN = _setting("rate_limit_token")  # 30/minute: refresh y logout
EXTERNAL = _setting("rate_limit_external")  # 30/minute: cada petición llama a PokeAPI
EXPORT = _setting("rate_limit_export")  # 10/minute: generar PDF consume CPU
DEFAULT = _setting("rate_limit_default")  # 100/minute: resto de endpoints autenticados

_settings = get_settings()
limiter = Limiter(
    key_func=rate_limit_key,
    headers_enabled=True,  # X-RateLimit-Limit, -Remaining, -Reset y Retry-After
    strategy="moving-window",  # ventana deslizante: sin ráfagas dobles entre minutos
    storage_uri=_settings.rate_limit_storage_uri,
    enabled=_settings.rate_limit_enabled,
)


async def rate_limit_exceeded_handler(request: Request, exc: Exception) -> JSONResponse:
    """Respuesta 429 en español, con las cabeceras que indican cuándo volver a intentarlo."""
    assert isinstance(exc, RateLimitExceeded)  # noqa: S101  (lo garantiza el registro)
    logger.warning(
        "Rate limit superado: %s %s clave=%s límite=%s",
        request.method,
        request.url.path,
        rate_limit_key(request),
        exc.detail,
    )
    response = JSONResponse(
        status_code=429,
        content={
            "detail": "Demasiadas peticiones. Espera un poco antes de volver a intentarlo.",
            "limit": exc.detail,
        },
    )
    return request.app.state.limiter._inject_headers(response, request.state.view_rate_limit)
