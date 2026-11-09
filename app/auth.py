"""Hash de contraseñas y emisión y validación de JWT.

Decisiones de seguridad (detalladas en ``SECURITY.md``):

- **bcrypt** con coste configurable (12 por defecto) mediante passlib. Nunca se guarda
  ni se registra una contraseña en claro.
- **Comparación de tiempo constante ante usuarios inexistentes**: si el usuario no
  existe se verifica igualmente contra un hash ficticio, para que el tiempo de respuesta
  no revele qué nombres de usuario están registrados.
- **JWT según RFC 8725**: algoritmo fijado explícitamente al decodificar (nunca se
  confía en la cabecera ``alg``), y validación de ``exp``, ``iat``, ``iss``, ``aud`` y del
  tipo de token, para que un refresh token no sirva como access token ni al revés.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import lru_cache
from typing import Any, Literal

from jose import JWTError, jwt
from passlib.context import CryptContext

from app.config import get_settings
from app.models import User, utc_now

TokenType = Literal["access", "refresh"]


class InvalidTokenError(Exception):
    """El token no es válido: firma, caducidad, emisor, audiencia o tipo incorrectos."""


# ═════════════════════════════════════════════════════════════
#  Contraseñas
# ═════════════════════════════════════════════════════════════
@lru_cache
def _password_context() -> CryptContext:
    return CryptContext(
        schemes=["bcrypt"],
        deprecated="auto",
        bcrypt__rounds=get_settings().bcrypt_rounds,
    )


@lru_cache
def _dummy_hash() -> str:
    return _password_context().hash("contraseña-ficticia-para-igualar-tiempos")


def hash_password(password: str) -> str:
    return _password_context().hash(password)


def password_needs_rehash(hashed_password: str) -> bool:
    """Indica si el hash se generó con parámetros antiguos (p. ej., un coste menor)."""
    return _password_context().needs_update(hashed_password)


def verify_password(password: str, hashed_password: str | None) -> bool:
    """Comprueba una contraseña. Con ``hashed_password=None`` gasta el mismo tiempo."""
    if hashed_password is None:
        _password_context().verify(password, _dummy_hash())
        return False
    return _password_context().verify(password, hashed_password)


# ═════════════════════════════════════════════════════════════
#  JWT
# ═════════════════════════════════════════════════════════════
@dataclass(frozen=True, slots=True)
class IssuedToken:
    token: str
    jti: str
    expires_at: datetime


def _encode(claims: dict[str, Any]) -> str:
    settings = get_settings()
    return jwt.encode(
        claims,
        settings.secret_key.get_secret_value(),
        algorithm=settings.jwt_algorithm,
    )


def _base_claims(user: User, token_type: TokenType, lifetime: timedelta) -> dict[str, Any]:
    settings = get_settings()
    now = utc_now()
    return {
        "sub": user.username,
        "user_id": user.id,
        "iat": int(now.timestamp()),
        "nbf": int(now.timestamp()),
        "exp": int((now + lifetime).timestamp()),
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
        "jti": uuid.uuid4().hex,
        "type": token_type,
    }


def create_access_token(user: User) -> IssuedToken:
    """Access token de corta duración (24 h por defecto, como pide el enunciado)."""
    lifetime = timedelta(minutes=get_settings().access_token_expire_minutes)
    claims = _base_claims(user, "access", lifetime)
    return IssuedToken(
        token=_encode(claims),
        jti=claims["jti"],
        expires_at=utc_now() + lifetime,
    )


def create_refresh_token(user: User, family_id: str) -> IssuedToken:
    """Refresh token de 7 días que pertenece a una familia de rotación."""
    lifetime = timedelta(days=get_settings().refresh_token_expire_days)
    claims = _base_claims(user, "refresh", lifetime) | {"fam": family_id}
    return IssuedToken(
        token=_encode(claims),
        jti=claims["jti"],
        expires_at=utc_now() + lifetime,
    )


def decode_token(token: str, expected_type: TokenType) -> dict[str, Any]:
    """Valida un JWT y devuelve sus claims, o lanza ``InvalidTokenError``.

    ``algorithms`` se fija explícitamente: así se rechazan los tokens con ``alg: none``
    y los ataques de confusión de algoritmo, sea cual sea la cabecera del token.
    """
    settings = get_settings()
    try:
        claims = jwt.decode(
            token,
            settings.secret_key.get_secret_value(),
            algorithms=[settings.jwt_algorithm],
            audience=settings.jwt_audience,
            issuer=settings.jwt_issuer,
            options={"require_exp": True, "require_iat": True, "require_sub": True},
        )
    except JWTError as exc:
        raise InvalidTokenError(str(exc)) from exc

    if claims.get("type") != expected_type:
        raise InvalidTokenError(f"Se esperaba un token de tipo {expected_type!r}")
    if not isinstance(claims.get("user_id"), int) or not claims.get("jti"):
        raise InvalidTokenError("Faltan claims obligatorios")
    return claims
