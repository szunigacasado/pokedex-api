"""Dependencias compartidas por los routers (sesión de base de datos y usuario actual)."""

import logging
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from sqlmodel import Session

from app.auth import InvalidTokenError, decode_token
from app.database import get_session
from app.models import User

logger = logging.getLogger("pokedex_api.auth")

# auto_error=False: la respuesta 401 la construimos nosotros, siempre con el mismo
# mensaje, para no dar pistas de por qué se ha rechazado el token.
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login", auto_error=False)

SessionDep = Annotated[Session, Depends(get_session)]


def _unauthorized() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="No autenticado o token no válido.",
        headers={"WWW-Authenticate": "Bearer"},
    )


def get_current_user(
    request: Request,
    session: SessionDep,
    token: Annotated[str | None, Depends(oauth2_scheme)],
) -> User:
    """Devuelve el usuario dueño del access token, o responde 401.

    Además de validar el token se comprueba contra la base de datos que el usuario
    sigue existiendo, está activo y conserva el mismo nombre que figura en el token.
    """
    if not token:
        raise _unauthorized()

    try:
        claims = decode_token(token, expected_type="access")
    except InvalidTokenError as exc:
        logger.warning(
            "Token rechazado en %s %s: %s", request.method, request.url.path, exc.__class__.__name__
        )
        raise _unauthorized() from exc

    user = session.get(User, claims["user_id"])
    if user is None or not user.is_active or user.username != claims["sub"]:
        logger.warning(
            "Token válido de un usuario inexistente o inactivo (id=%s)", claims["user_id"]
        )
        raise _unauthorized()
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]
