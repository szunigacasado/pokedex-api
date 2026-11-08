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
