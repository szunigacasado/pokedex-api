"""Middlewares de seguridad HTTP (ASGI puros).

``SecurityHeadersMiddleware`` añade a **todas** las respuestas (también a los errores y a
los rechazos de otros middlewares) las cabeceras que piden la guía OWASP *Secure Headers*
y el *API Security Top 10*:

=================================  ====================================================
``X-Content-Type-Options``         El navegador no «adivina» el tipo de contenido.
``X-Frame-Options`` / CSP          Nadie puede incrustar la API en un iframe
                                   (*clickjacking*).
``Referrer-Policy``                No se filtran URLs a otros sitios.
``Permissions-Policy``             Cámara, micrófono, ubicación y pagos desactivados.
``Cross-Origin-*-Policy``          Aísla la API de otros orígenes.
``Content-Security-Policy``        Muy estricta en la API; en /docs solo permite el CDN de
                                   Swagger (con SRI) y los recursos propios; en la web
                                   (/), solo código propio y las imágenes de PokeAPI.
``Strict-Transport-Security``      Solo si la petición llegó por HTTPS: obliga al
                                   navegador a usar siempre HTTPS durante dos años.
``Cache-Control: no-store``        Por defecto, ningún proxy ni navegador guarda las
                                   respuestas, que pueden contener datos personales.
=================================  ====================================================

``BodySizeLimitMiddleware`` rechaza con ``413`` los cuerpos demasiado grandes **antes de
cargarlos en memoria**: mira ``Content-Length`` y, si no viene (``Transfer-Encoding:
chunked``), cuenta los bytes según llegan y corta al pasarse.
"""

import ipaddress
import json
from typing import Final

from starlette.types import ASGIApp, Message, Receive, Scope, Send

API_CSP: Final = "default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
DOCS_CSP: Final = (
    "default-src 'none'; "
    "script-src 'self' https://cdn.jsdelivr.net; "
    # Swagger UI aplica estilos en línea al pintar la página; los scripts en línea, que
    # son el riesgo real, siguen prohibidos.
    "style-src 'self' https://cdn.jsdelivr.net 'unsafe-inline'; "
    "img-src 'self' data:; font-src 'self' data:; connect-src 'self'; "
    "frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
)
# Web de la Pokédex: solo código propio. Sin scripts ni estilos en línea y sin CDN; las
# imágenes y los gritos de los Pokémon vienen de los repositorios de PokeAPI en GitHub.
WEB_CSP: Final = (
    "default-src 'none'; script-src 'self'; style-src 'self'; font-src 'self'; "
    "img-src 'self' data: https://raw.githubusercontent.com; "
    "media-src https://raw.githubusercontent.com; connect-src 'self'; manifest-src 'self'; "
    "frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
)
STATIC_CACHE: Final = "public, max-age=86400"
# Los ficheros de la web se revalidan en cada carga (304 si no han cambiado): así una
# versión nueva llega al momento y no se queda una antigua en la caché del navegador.
WEB_CACHE: Final = "no-cache"
WEB_PREFIXES: Final = ("/web/", "/fonts/")
WEB_PAGES: Final = ("/", "/privacidad")

_BASE_HEADERS: Final = (
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"no-referrer"),
    (b"permissions-policy", b"camera=(), microphone=(), geolocation=(), payment=(), usb=()"),
    (b"cross-origin-opener-policy", b"same-origin"),
    (b"cross-origin-resource-policy", b"same-origin"),
    (b"x-permitted-cross-domain-policies", b"none"),
)


class SecurityHeadersMiddleware:
    def __init__(self, app: ASGIApp, hsts_max_age: int) -> None:
        self.app = app
        self.hsts = f"max-age={hsts_max_age}; includeSubDomains".encode()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path: str = scope["path"]
        csp = DOCS_CSP if path == "/docs" else WEB_CSP if path in WEB_PAGES else API_CSP
        is_https = scope.get("scheme") == "https"

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = [
                    (k, v)
                    for k, v in message.get("headers", [])
                    # Si algo intentara anunciar el servidor o la tecnología, se elimina.
                    if k.lower() not in (b"server", b"x-powered-by")
                ]
                present = {k.lower() for k, _ in headers}
                headers.extend(_BASE_HEADERS)
                headers.append((b"content-security-policy", csp.encode()))
                if is_https:
                    headers.append((b"strict-transport-security", self.hsts))
                if b"cache-control" not in present:
                    if path.startswith("/static/"):
                        cache = STATIC_CACHE
                    elif path.startswith(WEB_PREFIXES):
                        cache = WEB_CACHE
                    else:
                        cache = "no-store"
                    headers.append((b"cache-control", cache.encode()))
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_with_headers)


class BodySizeLimitMiddleware:
    def __init__(self, app: ASGIApp, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        for name, value in scope.get("headers", []):
            if name == b"content-length":
                try:
                    declared = int(value)
                except ValueError:
                    await self._reject(send, 400, "Cabecera Content-Length no válida.")
                    return
                if declared > self.max_bytes:
                    await self._reject(send, 413, self._too_large_message())
                    return

        # Sin Content-Length (Transfer-Encoding: chunked) se cuentan los bytes según llegan.
        # No basta con lanzar una excepción al pasarse: FastAPI captura cualquier error al
        # leer el cuerpo y lo convierte en un 400 genérico. Por eso, al superar el límite,
        # el middleware responde él mismo 413, le dice a la aplicación que el cliente se ha
        # desconectado para que deje de leer y descarta lo que la aplicación intente enviar.
        received = 0
        rejected = False
        response_started = False

        async def counting_receive() -> Message:
            nonlocal received, rejected
            if rejected:
                return {"type": "http.disconnect"}
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes and not response_started:
                    rejected = True
                    await self._reject(send, 413, self._too_large_message())
                    return {"type": "http.disconnect"}
            return message

        async def guarded_send(message: Message) -> None:
            nonlocal response_started
            if rejected:
                return
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        await self.app(scope, counting_receive, guarded_send)

    def _too_large_message(self) -> str:
        return f"La petición es demasiado grande (máximo {self.max_bytes // 1024} KB)."

    @staticmethod
    async def _reject(send: Send, status: int, detail: str) -> None:
        body = json.dumps({"detail": detail}, ensure_ascii=False).encode()
        await send(
            {
                "type": "http.response.start",
                "status": status,
                "headers": [
                    (b"content-type", b"application/json; charset=utf-8"),
                    (b"content-length", str(len(body)).encode()),
                    (b"connection", b"close"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})


class TrustedProxyMiddleware:
    """IP real del visitante y HTTPS cuando la API solo es accesible a través de un proxy.

    En la plataforma de despliegue, todas las peticiones llegan desde su proxy: sin esto,
    todos los visitantes compartirían su IP (y con ella los límites de intentos) y la API
    no sabría que la conexión original era HTTPS (sin HSTS).

    - La IP se toma **solo** de ``CLIENT_IP_HEADER`` (por ejemplo ``cf-connecting-ip``),
      una cabecera que el proxy escribe siempre y que el cliente no puede falsear porque
      el proxy la sobrescribe. ``X-Forwarded-For`` no sirve: el cliente puede escribir lo
      que quiera y el proxy solo añade al final.
    - El esquema se toma de ``X-Forwarded-Proto`` solo si viene esa cabecera de confianza.
    - Sin ``CLIENT_IP_HEADER`` (en local o con Docker y Caddy) no se activa.
    """

    def __init__(self, app: ASGIApp, header: str) -> None:
        self.app = app
        self.header = header.lower().encode("latin-1")

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] in ("http", "websocket"):
            headers = dict(scope.get("headers", []))
            ip = _valid_ip(headers.get(self.header, b"").decode("latin-1").strip())
            if ip is not None:
                client = scope.get("client") or (ip, 0)
                scope = {**scope, "client": (ip, client[1])}
                if headers.get(b"x-forwarded-proto") == b"https":
                    scope["scheme"] = "wss" if scope["type"] == "websocket" else "https"
        await self.app(scope, receive, send)


def _valid_ip(value: str) -> str | None:
    try:
        return str(ipaddress.ip_address(value))
    except ValueError:
        return None
