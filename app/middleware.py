"""Middleware de registro de peticiones.

Es un middleware **ASGI puro** y no ``BaseHTTPMiddleware``: no copia el cuerpo de las
respuestas, no rompe las respuestas en streaming y añade menos latencia.

Está **protegido** frente a fallos:

- Si un endpoint lanza una excepción no controlada, se registra con su traza completa y
  el cliente recibe un ``500`` genérico con el ID de petición. Nunca recibe la traza, que
  revelaría rutas, librerías o consultas internas.
- Si la respuesta ya había empezado a enviarse cuando ocurre el error, no se puede
  cambiar; se registra y se cierra la conexión.
- El registro de la petición se hace en ``finally``: ocurre siempre, también con errores.
"""

import json
import logging
import re
import time
import uuid
from typing import Final
from urllib.parse import parse_qsl, urlencode

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.logging_config import request_id_var

logger = logging.getLogger("pokedex_api.http")

REQUEST_ID_HEADER: Final = "x-request-id"
_VALID_REQUEST_ID: Final = re.compile(r"^[A-Za-z0-9._-]{8,64}$")
_SENSITIVE_PARAMS: Final = frozenset({"token", "access_token", "refresh_token", "password", "code"})


def _incoming_request_id(scope: Scope) -> str:
    """Reutiliza el X-Request-ID del cliente o del proxy si es seguro; si no, crea uno.

    Se valida el formato para que nadie pueda inyectar saltos de línea u otros
    caracteres en los logs (inyección de logs, CWE-117).
    """
    for name, value in scope.get("headers", []):
        if name == REQUEST_ID_HEADER.encode():
            candidate = value.decode("latin-1")
            if _VALID_REQUEST_ID.fullmatch(candidate):
                return candidate
    return uuid.uuid4().hex


def _safe_query(raw: bytes) -> str:
    """Cadena de consulta con los parámetros sensibles ocultos."""
    if not raw:
        return ""
    pairs = parse_qsl(raw.decode("latin-1"), keep_blank_values=True)
    return urlencode([(k, "[REDACTED]" if k.lower() in _SENSITIVE_PARAMS else v) for k, v in pairs])


class RequestLoggingMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = _incoming_request_id(scope)
        token = request_id_var.set(request_id)
        method, path = scope["method"], scope["path"]
        query = _safe_query(scope.get("query_string", b""))
        client_ip = (scope.get("client") or ("-",))[0]
        started = time.perf_counter()
        status_code = 500
        response_started = False

        logger.info(
            "Request: %s %s",
            method,
            path,
            extra={"method": method, "path": path, "query": query, "client_ip": client_ip},
        )

        async def send_with_request_id(message: Message) -> None:
            nonlocal status_code, response_started
            if message["type"] == "http.response.start":
                status_code = message["status"]
                response_started = True
                headers = list(message.get("headers", []))
                headers.append((REQUEST_ID_HEADER.encode(), request_id.encode()))
                message = {**message, "headers": headers}
            await send(message)

        try:
            await self.app(scope, receive, send_with_request_id)
        except Exception:
            logger.exception("Error no controlado en %s %s", method, path)
            if response_started:
                raise
            status_code = 500
            await self._send_internal_error(send, request_id)
        finally:
            duration = time.perf_counter() - started
            user_id = (scope.get("state") or {}).get("user_id")
            level = (
                logging.ERROR
                if status_code >= 500
                else logging.WARNING
                if status_code >= 400
                else logging.INFO
            )
            logger.log(
                level,
                "Response: %s | Duration: %.3fs",
                status_code,
                duration,
                extra={
                    "method": method,
                    "path": path,
                    "status_code": status_code,
                    "duration_ms": round(duration * 1000, 1),
                    "client_ip": client_ip,
                    "user_id": user_id,
                },
            )
            request_id_var.reset(token)

    @staticmethod
    async def _send_internal_error(send: Send, request_id: str) -> None:
        body = json.dumps(
            {
                "detail": "Error interno del servidor. Si persiste, indica este ID de petición.",
                "request_id": request_id,
            },
            ensure_ascii=False,
        ).encode()
        await send(
            {
                "type": "http.response.start",
                "status": 500,
                "headers": [
                    (b"content-type", b"application/json; charset=utf-8"),
                    (b"content-length", str(len(body)).encode()),
                    (REQUEST_ID_HEADER.encode(), request_id.encode()),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})
