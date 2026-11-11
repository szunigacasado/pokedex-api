"""Endpoints de autenticación: registro, login, renovación de tokens y logout."""

import logging
import math
import uuid
from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, Body, Depends, HTTPException, Request, Response, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlmodel import or_, select

from app import rate_limit as rl
from app.auth import (
    InvalidTokenError,
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    password_needs_rehash,
    verify_password,
)
from app.config import get_settings
from app.dependencies import CurrentUser, SessionDep
from app.models import RefreshRequest, RefreshToken, TokenPair, User, UserCreate, UserRead, utc_now
from app.rate_limit import limiter
from app.session_cookie import (
    clear_refresh_cookie,
    refresh_token_from_cookie,
    set_refresh_cookie,
)

logger = logging.getLogger("pokedex_api.auth")

router = APIRouter(prefix="/auth", tags=["Autenticación"])

INVALID_CREDENTIALS = "Usuario o contraseña incorrectos."
INVALID_SESSION = "La sesión no es válida o ha caducado. Inicia sesión de nuevo."


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "desconocida"


def issue_token_pair(session: SessionDep, user: User, family_id: str) -> TokenPair:
    """Emite un access token y un refresh token y registra este último."""
    access = create_access_token(user)
    refresh = create_refresh_token(user, family_id)
    session.add(
        RefreshToken(
            jti=refresh.jti,
            family_id=family_id,
            user_id=user.id,
            expires_at=refresh.expires_at,
        )
    )
    session.commit()
    return TokenPair(
        access_token=access.token,
        refresh_token=refresh.token,
        expires_in=get_settings().access_token_expire_minutes * 60,
    )


def _revoke_family(session: SessionDep, family_id: str) -> None:
    now = utc_now()
    tokens = session.exec(
        select(RefreshToken).where(
            RefreshToken.family_id == family_id,
            RefreshToken.revoked_at.is_(None),  # type: ignore[union-attr]
        )
    ).all()
    for token in tokens:
        token.revoked_at = now
        session.add(token)
    session.commit()


# ═════════════════════════════════════════════════════════════
#  Registro
# ═════════════════════════════════════════════════════════════
@router.post(
    "/register",
    response_model=UserRead,
    status_code=status.HTTP_201_CREATED,
    summary="Registrar un usuario",
    responses={409: {"description": "El nombre de usuario o el email ya existen"}},
)
@limiter.limit(rl.REGISTER)
def register(
    response: Response, payload: UserCreate, session: SessionDep, request: Request
) -> User:
    """Crea una cuenta nueva.

    - **username**: 3-50 caracteres (letras, números, `.`, `_`, `-`). Se guarda en minúsculas.
    - **email**: formato validado con expresión regular. Se guarda en minúsculas.
    - **password**: mínimo 8 caracteres, al menos una mayúscula y un número, sin incluir
      el nombre de usuario. Se guarda con bcrypt y nunca en claro.
    """
    existing = session.exec(
        select(User).where(or_(User.username == payload.username, User.email == payload.email))
    ).first()
    if existing is not None:
        logger.info("Registro rechazado por duplicado desde %s", _client_ip(request))
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="El nombre de usuario o el email ya están registrados.",
        )

    user = User(
        username=payload.username,
        email=payload.email,
        hashed_password=hash_password(payload.password),
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    logger.info("Usuario registrado: id=%s", user.id)
    return user


# ═════════════════════════════════════════════════════════════
#  Login
# ═════════════════════════════════════════════════════════════
@router.post(
    "/login",
    response_model=TokenPair,
    summary="Iniciar sesión",
    responses={
        401: {"description": "Credenciales incorrectas"},
        429: {"description": "Cuenta bloqueada temporalmente por demasiados intentos fallidos"},
    },
)
@limiter.limit(rl.LOGIN)
def login(
    form: Annotated[OAuth2PasswordRequestForm, Depends()],
    session: SessionDep,
    request: Request,
    response: Response,
) -> TokenPair:
    """Devuelve un **access token** (24 h) y un **refresh token** (7 días).

    Sigue el estándar OAuth2 *password flow* (formulario `username` / `password`), así que
    el botón **Authorize** de Swagger funciona directamente. En `username` se acepta
    también el email.

    Tras varios intentos fallidos seguidos la cuenta se bloquea unos minutos y se responde
    `429` con la cabecera `Retry-After`.
    """
    settings = get_settings()
    identifier = form.username.strip().lower()
    user = session.exec(
        select(User).where(or_(User.username == identifier, User.email == identifier))
    ).first()

    now = utc_now()
    if user is not None and user.locked_until is not None and user.locked_until > now:
        retry_after = math.ceil((user.locked_until - now).total_seconds())
        logger.warning("Login sobre cuenta bloqueada: id=%s ip=%s", user.id, _client_ip(request))
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Demasiados intentos fallidos. Vuelve a intentarlo más tarde.",
            headers={"Retry-After": str(retry_after)},
        )

    password_ok = verify_password(form.password, user.hashed_password if user else None)

    if user is None or not password_ok or not user.is_active:
        if user is not None and not password_ok:
            user.failed_login_attempts += 1
            if user.failed_login_attempts >= settings.max_failed_logins:
                user.locked_until = now + timedelta(minutes=settings.lockout_minutes)
                user.failed_login_attempts = 0
                logger.warning("Cuenta bloqueada por intentos fallidos: id=%s", user.id)
            session.add(user)
            session.commit()
        logger.warning("Login fallido desde %s", _client_ip(request))
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=INVALID_CREDENTIALS,
            headers={"WWW-Authenticate": "Bearer"},
        )

    user.failed_login_attempts = 0
    user.locked_until = None
    if password_needs_rehash(user.hashed_password):
        user.hashed_password = hash_password(form.password)
    session.add(user)

    response.headers["Cache-Control"] = "no-store"
    logger.info("Login correcto: id=%s", user.id)
    pair = issue_token_pair(session, user, family_id=uuid.uuid4().hex)
    # Desde el propio sitio (la web o /docs), también en cookie HttpOnly: así iniciar
    # sesión en /docs la inicia en la web. Ver app/session_cookie.py.
    set_refresh_cookie(request, response, pair.refresh_token)
    return pair


# ═════════════════════════════════════════════════════════════
#  Renovación y cierre de sesión
# ═════════════════════════════════════════════════════════════
OptionalRefresh = Annotated[RefreshRequest | None, Body()]


def _refresh_token(request: Request, payload: RefreshRequest | None) -> str:
    """El del cuerpo; si no hay, el de la cookie de sesión del propio sitio."""
    token = payload.refresh_token if payload else refresh_token_from_cookie(request)
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail=INVALID_SESSION)
    return token


def _load_refresh_token(session: SessionDep, raw_token: str) -> tuple[dict, RefreshToken]:
    try:
        claims = decode_token(raw_token, expected_type="refresh")
    except InvalidTokenError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail=INVALID_SESSION) from exc

    stored = session.exec(select(RefreshToken).where(RefreshToken.jti == claims["jti"])).first()
    if stored is None or stored.user_id != claims["user_id"]:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail=INVALID_SESSION)
    return claims, stored


@router.post(
    "/refresh",
    response_model=TokenPair,
    summary="Renovar los tokens",
    responses={401: {"description": "Refresh token no válido, caducado o ya usado"}},
)
@limiter.limit(rl.TOKEN)
def refresh(
    request: Request, session: SessionDep, response: Response, payload: OptionalRefresh = None
) -> TokenPair:
    """Cambia un refresh token por un par de tokens nuevo (**rotación**).

    Cada refresh token sirve una sola vez. Si se presenta uno ya usado, se interpreta
    como un posible robo y se revocan todas las sesiones que derivan del mismo login.

    El refresh token va en el cuerpo. Desde la propia web también vale la cookie de
    sesión (`HttpOnly`), y entonces el cuerpo puede ir vacío.
    """
    _, stored = _load_refresh_token(session, _refresh_token(request, payload))

    if stored.revoked_at is not None:
        logger.warning(
            "Reutilización de refresh token: se revoca la familia (user_id=%s)", stored.user_id
        )
        _revoke_family(session, stored.family_id)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail=INVALID_SESSION)

    user = session.get(User, stored.user_id)
    if user is None or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail=INVALID_SESSION)

    stored.revoked_at = utc_now()
    session.add(stored)
    response.headers["Cache-Control"] = "no-store"
    pair = issue_token_pair(session, user, family_id=stored.family_id)
    set_refresh_cookie(request, response, pair.refresh_token)
    return pair


@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Cerrar sesión",
    responses={401: {"description": "Refresh token no válido"}},
)
@limiter.limit(rl.TOKEN)
def logout(
    request: Request, response: Response, session: SessionDep, payload: OptionalRefresh = None
) -> None:
    """Revoca el refresh token y todos los de su misma sesión.

    El access token sigue siendo válido hasta que caduca: es la contrapartida de usar
    tokens sin estado. Por eso el refresh token, que es el de larga duración, sí se
    controla en la base de datos.
    """
    clear_refresh_cookie(request, response)
    _, stored = _load_refresh_token(session, _refresh_token(request, payload))
    _revoke_family(session, stored.family_id)


# ═════════════════════════════════════════════════════════════
#  Perfil
# ═════════════════════════════════════════════════════════════
@router.get(
    "/me",
    response_model=UserRead,
    summary="Usuario autenticado",
    responses={401: {"description": "Sin token o token no válido"}},
)
@limiter.limit(rl.DEFAULT)
def read_me(request: Request, response: Response, current_user: CurrentUser) -> User:
    """Devuelve los datos del usuario dueño del access token."""
    return current_user
