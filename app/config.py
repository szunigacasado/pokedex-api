"""Configuración de la aplicación leída de variables de entorno.

Toda la configuración sensible (claves, cadenas de conexión) llega por entorno o por
un fichero ``.env`` local que nunca se versiona. En producción la aplicación se niega a
arrancar con una clave débil o con la de ejemplo: es mejor fallar al inicio que firmar
tokens con un secreto adivinable.
"""

import json
from datetime import UTC, datetime
from functools import lru_cache
from typing import Annotated, Literal

from limits import parse_many
from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

Environment = Literal["development", "production", "test"]
LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]

#: Longitud mínima de la clave de firma. HS256 usa una clave de 256 bits (32 bytes).
MIN_SECRET_KEY_LENGTH = 32

#: Marca que identifica la clave de ejemplo de ``.env.example``.
_EXAMPLE_SECRET_MARKER = "cambia-esto"  # noqa: S105  (no es una clave, es lo que se busca)


class Settings(BaseSettings):
    """Parámetros de configuración validados al arrancar."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_name: str = "Pokédex API"
    environment: Environment = "development"

    # ── Logging ──────────────────────────────────────────────
    log_level: LogLevel = "INFO"
    log_format: Literal["text", "json"] = Field(
        default="text", description="text para leerlo en consola; json para un agregador."
    )
    log_file: str = Field(
        default="pokedex_api.log", description="Fichero de log; vacío para no escribir fichero."
    )
    log_max_bytes: int = Field(default=5 * 1024 * 1024, ge=1024)
    log_backup_count: int = Field(default=3, ge=0, le=50)

    secret_key: SecretStr = Field(
        description="Clave para firmar los JWT. Obligatoria: no tiene valor por defecto.",
    )
    database_url: str = "sqlite:///./pokedex.db"

    # ── JWT ──────────────────────────────────────────────────
    # Solo algoritmos HMAC: la clave es simétrica y no existe clave pública que un
    # atacante pueda usar para un ataque de confusión de algoritmo.
    jwt_algorithm: Literal["HS256", "HS384", "HS512"] = "HS256"
    jwt_issuer: str = "pokedex-api"
    jwt_audience: str = "pokedex-api-clients"
    access_token_expire_minutes: int = Field(default=24 * 60, ge=1, le=7 * 24 * 60)
    refresh_token_expire_days: int = Field(default=7, ge=1, le=90)

    # ── Contraseñas e intentos de inicio de sesión ───────────
    bcrypt_rounds: int = Field(default=12, ge=4, le=16)
    max_failed_logins: int = Field(default=5, ge=1, le=20)
    lockout_minutes: int = Field(default=15, ge=1, le=24 * 60)

    # ── Inicio de sesión con Google (OpenID Connect) ─────────
    # Sin GOOGLE_CLIENT_ID y GOOGLE_CLIENT_SECRET, el login con Google responde 503 y el
    # resto de la API funciona igual.
    google_client_id: str | None = None
    google_client_secret: SecretStr | None = None
    google_redirect_uri: str = "http://localhost:8000/api/v1/auth/google/callback"
    oauth_success_redirect: str | None = Field(
        default=None,
        description="Si se indica, tras el login se redirige aquí con los tokens en el "
        "fragmento (#access_token=...); si no, el callback devuelve los tokens en JSON.",
    )

    @property
    def google_enabled(self) -> bool:
        return bool(self.google_client_id and self.google_client_secret)

    # ── Cliente de PokeAPI ───────────────────────────────────
    pokeapi_base_url: str = "https://pokeapi.co/api/v2"
    pokeapi_timeout_seconds: float = Field(default=5.0, gt=0, le=60)
    pokeapi_max_retries: int = Field(default=3, ge=0, le=10)
    pokeapi_backoff_seconds: float = Field(default=0.3, ge=0, le=10)
    pokeapi_max_concurrency: int = Field(default=10, ge=1, le=50)
    pokeapi_cache_ttl_seconds: int = Field(default=3600, ge=0)
    pokeapi_cache_max_entries: int = Field(default=2048, ge=16)
    pokeapi_breaker_threshold: int = Field(default=5, ge=1, le=100)
    pokeapi_breaker_cooldown_seconds: float = Field(default=30.0, gt=0)

    # ── Rate limiting ────────────────────────────────────────
    # Formato de la librería «limits»: "N/second|minute|hour|day". Se pueden combinar
    # varios con ";", p. ej. "10/minute;100/hour".
    rate_limit_enabled: bool = True
    rate_limit_storage_uri: str = Field(
        default="memory://",
        description="memory:// en un solo proceso; redis://host:6379 si hay varias réplicas.",
    )
    rate_limit_register: str = "5/hour"
    rate_limit_login: str = "10/minute"
    rate_limit_token: str = "30/minute"  # noqa: S105  (límite de /refresh y /logout)
    rate_limit_external: str = "30/minute"
    rate_limit_export: str = "10/minute"
    rate_limit_default: str = "100/minute"

    # ── CORS ─────────────────────────────────────────────────
    # Lista separada por comas o JSON. Nunca "*" en producción.
    cors_origins: Annotated[list[str], NoDecode] = [
        "http://localhost:3000",
        "http://localhost:5173",
    ]
    cors_max_age_seconds: int = Field(default=3600, ge=0, le=86400)

    # ── Seguridad HTTP ───────────────────────────────────────
    # Hosts permitidos en la cabecera Host (protege de Host header injection y de DNS
    # rebinding). Admite comodines de subdominio: *.ejemplo.com
    allowed_hosts: Annotated[list[str], NoDecode] = ["localhost", "127.0.0.1", "[::1]"]
    max_body_bytes: int = Field(default=1024 * 1024, ge=1024, le=50 * 1024 * 1024)
    hsts_max_age_seconds: int = Field(default=63_072_000, ge=0)  # 2 años
    docs_enabled: bool = Field(default=True, description="Publicar /docs, /redoc y /openapi.json")

    # ── Versionado ───────────────────────────────────────────
    # GET /api/v1/pokedex está obsoleto desde la publicación de la v2 y se retira en la
    # fecha de Sunset; a partir de entonces responde 410 Gone.
    v1_pokedex_deprecated_at: datetime = datetime(2026, 11, 9, tzinfo=UTC)
    v1_pokedex_sunset: datetime = datetime(2027, 6, 30, 23, 59, 59, tzinfo=UTC)

    @field_validator("allowed_hosts", mode="before")
    @classmethod
    def _split_hosts(cls, value: object) -> object:
        if isinstance(value, str):
            value = value.strip()
            if value.startswith("["):
                return json.loads(value)
            return [host.strip().lower() for host in value.split(",") if host.strip()]
        return value

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        if isinstance(value, str):
            value = value.strip()
            if value.startswith("["):
                return json.loads(value)
            return [origin.strip().rstrip("/") for origin in value.split(",") if origin.strip()]
        return value

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")

    @model_validator(mode="after")
    def _check_secret_key_strength(self) -> "Settings":
        secret = self.secret_key.get_secret_value()
        if len(secret) < MIN_SECRET_KEY_LENGTH:
            raise ValueError(f"SECRET_KEY debe tener al menos {MIN_SECRET_KEY_LENGTH} caracteres.")
        if self.is_production and _EXAMPLE_SECRET_MARKER in secret:
            raise ValueError("SECRET_KEY sigue siendo la de .env.example; genera una propia.")
        return self

    @model_validator(mode="after")
    def _check_rate_limits(self) -> "Settings":
        """Un límite mal escrito se detecta al arrancar, no en la primera petición."""
        for name, value in self.model_dump().items():
            if name.startswith("rate_limit_") and isinstance(value, str) and "://" not in value:
                try:
                    parse_many(value)
                except ValueError as exc:
                    raise ValueError(f"{name.upper()} no es un límite válido: {value!r}") from exc
        return self

    @model_validator(mode="after")
    def _check_allowed_hosts(self) -> "Settings":
        if self.is_production and "*" in self.allowed_hosts:
            raise ValueError("ALLOWED_HOSTS no puede incluir '*' en producción.")
        return self

    @model_validator(mode="after")
    def _check_cors(self) -> "Settings":
        for origin in self.cors_origins:
            if origin == "*":
                if self.is_production:
                    raise ValueError("CORS_ORIGINS no puede ser '*' en producción.")
                continue
            if not origin.startswith(("http://", "https://")):
                raise ValueError(f"Origen CORS no válido: {origin!r} (falta http:// o https://)")
        return self


@lru_cache
def get_settings() -> Settings:
    """Devuelve la configuración, que se lee una sola vez por proceso."""
    return Settings()  # type: ignore[call-arg]  # los valores llegan del entorno
