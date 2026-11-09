"""Configuración del logging de la aplicación.

- Todos los loggers de la aplicación cuelgan de ``pokedex_api`` (``pokedex_api.auth``,
  ``pokedex_api.pokeapi``...), así que se configuran en un único sitio.
- Salida a consola y a un fichero con rotación (``pokedex_api.log``, 5 MB x 3 copias),
  con el formato del enunciado o en JSON para un agregador de logs.
- Cada línea lleva el **ID de la petición** que la generó, de modo que se puede seguir
  una petición de principio a fin (entrada, consulta a PokeAPI, error, respuesta).
- Un **filtro de redacción** oculta tokens, JWT y contraseñas aunque algún mensaje los
  incluyera por error. Es una defensa en profundidad: el código ya evita registrarlos.
"""

import contextvars
import json
import logging
import re
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from typing import Final

from app.config import Settings

APP_LOGGER: Final = "pokedex_api"
TEXT_FORMAT: Final = "%(asctime)s - %(name)s - %(levelname)s - [%(request_id)s] %(message)s"

#: ID de la petición en curso. Las corrutinas de cada petición tienen su propia copia.
request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")

_REDACTIONS: Final = (
    # Cabecera Authorization: "Bearer <token>"
    (re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]+"), r"\1[REDACTED]"),
    # Cualquier JWT (tres bloques base64url separados por puntos, el primero empieza por eyJ)
    (re.compile(r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]*"), "[REDACTED_JWT]"),
    # password=..., "password": "...", secret_key=..., refresh_token: ...
    (
        re.compile(
            r"(?i)(\"?(?:password|passwd|secret(?:_key)?|api_key|access_token|refresh_token)\"?"
            r"\s*[:=]\s*\"?)[^\s\",&]+"
        ),
        r"\1[REDACTED]",
    ),
)

#: Campos estructurados que el middleware añade con ``extra=`` y que el JSON incluye.
_EXTRA_FIELDS: Final = (
    "method",
    "path",
    "status_code",
    "duration_ms",
    "client_ip",
    "user_id",
    "query",
)


def redact(text: str) -> str:
    for pattern, replacement in _REDACTIONS:
        text = pattern.sub(replacement, text)
    return text


class RequestContextFilter(logging.Filter):
    """Añade el ID de petición a cada registro y oculta los secretos del mensaje."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        message = record.getMessage()
        cleaned = redact(message)
        if cleaned != message:
            record.msg, record.args = cleaned, None
        return True


class JsonFormatter(logging.Formatter):
    """Una línea JSON por registro, lista para Loki, ELK o CloudWatch."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "request_id": getattr(record, "request_id", "-"),
            "message": record.getMessage(),
        }
        for field in _EXTRA_FIELDS:
            if hasattr(record, field):
                payload[field] = getattr(record, field)
        if record.exc_info:
            payload["exception"] = redact(self.formatException(record.exc_info))
        return json.dumps(payload, ensure_ascii=False, default=str)


class RedactingTextFormatter(logging.Formatter):
    """Formato de texto del enunciado; también limpia las trazas de excepción."""

    def formatException(self, ei) -> str:
        return redact(super().formatException(ei))


def configure_logging(settings: Settings) -> logging.Logger:
    """Configura el logger de la aplicación. Se puede llamar varias veces sin duplicar
    los handlers (por ejemplo, al crear varias aplicaciones en los tests)."""
    logger = logging.getLogger(APP_LOGGER)
    logger.setLevel(settings.log_level)
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()

    formatter: logging.Formatter = (
        JsonFormatter() if settings.log_format == "json" else RedactingTextFormatter(TEXT_FORMAT)
    )
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if settings.log_file:
        handlers.append(
            RotatingFileHandler(
                settings.log_file,
                maxBytes=settings.log_max_bytes,
                backupCount=settings.log_backup_count,
                encoding="utf-8",
            )
        )
    for handler in handlers:
        handler.setFormatter(formatter)
        handler.addFilter(RequestContextFilter())
        logger.addHandler(handler)
    return logger
