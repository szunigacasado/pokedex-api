"""Configuración de la aplicación leída de variables de entorno.

Toda la configuración sensible (claves, cadenas de conexión) llega por entorno o por
un fichero ``.env`` local que nunca se versiona. En producción la aplicación se niega a
arrancar con una clave débil o con la de ejemplo: es mejor fallar al inicio que firmar
tokens con un secreto adivinable.
"""

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

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
    log_level: LogLevel = "INFO"

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


@lru_cache
def get_settings() -> Settings:
    """Devuelve la configuración, que se lee una sola vez por proceso."""
    return Settings()  # type: ignore[call-arg]  # los valores llegan del entorno
