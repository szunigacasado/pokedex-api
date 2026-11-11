"""Sesión del navegador: el refresh token en una cookie ``HttpOnly``.

Con ella, entrar desde ``/docs`` (botón *Authorize*) deja también la sesión iniciada en la
web, y la web ya no guarda el refresh token en ningún sitio que pueda leer JavaScript.

Medidas:

- ``HttpOnly``: ningún script, ni siquiera uno inyectado, puede leerla.
- ``Secure`` por HTTPS y ``SameSite=Strict``: no viaja por HTTP ni en peticiones que
  empiecen en otro sitio.
- ``Path=/api/v1/auth``: solo se envía a los endpoints de sesión, no a toda la API.
- **Solo para el propio sitio**: la cookie únicamente se emite y se acepta si la petición
  viene de este mismo origen (cabeceras ``Origin`` o ``Sec-Fetch-Site``). Eso cierra el
  CSRF y el *login CSRF* (que una web ajena te deje con la sesión de otro).

Los clientes de la API (``curl``, Postman…) no envían esas cabeceras: siguen usando el
refresh token del cuerpo de la respuesta, como pide el enunciado.
"""

from typing import Final
from urllib.parse import urlsplit

from fastapi import Request, Response

from app.config import get_settings

REFRESH_COOKIE: Final = "pokedex_refresh"
REFRESH_COOKIE_PATH: Final = "/api/v1/auth"


def _secure(request: Request) -> bool:
    return request.url.scheme == "https" or get_settings().is_production


def is_same_origin(request: Request) -> bool:
    """¿La petición la hace una página de este mismo sitio?"""
    origin = request.headers.get("origin")
    if origin:
        parts = urlsplit(origin)
        host = request.headers.get("host", "").lower()
        return parts.scheme == request.url.scheme and parts.netloc.lower() == host
    # Sin Origin (algunas peticiones GET o navegadores antiguos): Fetch Metadata
    return request.headers.get("sec-fetch-site") == "same-origin"


def set_refresh_cookie(request: Request, response: Response, refresh_token: str) -> None:
    """Guarda el refresh token en la cookie, solo si la petición es del propio sitio."""
    if not is_same_origin(request):
        return
    response.set_cookie(
        REFRESH_COOKIE,
        refresh_token,
        max_age=get_settings().refresh_token_expire_days * 24 * 3600,
        path=REFRESH_COOKIE_PATH,
        httponly=True,
        secure=_secure(request),
        samesite="strict",
    )


def clear_refresh_cookie(request: Request, response: Response) -> None:
    response.delete_cookie(
        REFRESH_COOKIE,
        path=REFRESH_COOKIE_PATH,
        httponly=True,
        secure=_secure(request),
        samesite="strict",
    )


def refresh_token_from_cookie(request: Request) -> str | None:
    """El refresh token de la cookie, pero solo si la petición es del propio sitio."""
    token = request.cookies.get(REFRESH_COOKIE)
    return token if token and is_same_origin(request) else None
