"""Inicio de sesión con Google (OpenID Connect, authorization code + PKCE).

1. ``GET /auth/google/login`` genera ``state``, ``nonce`` y el par PKCE, los guarda en una
   cookie **firmada, HttpOnly y de 10 minutos** y redirige a Google.
2. El usuario elige su cuenta en Google, que lo devuelve a ``/auth/google/callback`` con
   un código de un solo uso.
3. El callback comprueba el ``state`` contra la cookie, canjea el código (con el
   ``code_verifier``), verifica el ID token y emite **nuestros propios** tokens, igual
   que el login con contraseña. El token de Google no se usa para nada más.

No hace falta guardar sesiones en el servidor: todo lo temporal va en la cookie firmada.
"""

import logging
import re
import secrets
import uuid
from datetime import timedelta
from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse, RedirectResponse, Response
from jose import JWTError, jwt
from pydantic import BaseModel
from sqlmodel import select

from app import rate_limit as rl
from app.auth import unusable_password
from app.config import get_settings
from app.dependencies import SessionDep
from app.models import USERNAME_PATTERN, OAuthAccount, User, utc_now
from app.rate_limit import limiter
from app.routers.auth import issue_token_pair
from app.services.google_oauth import (
    GoogleAuthError,
    GoogleIdentity,
    GoogleOAuthClient,
    new_pkce_pair,
)

logger = logging.getLogger("pokedex_api.oauth")

router = APIRouter(prefix="/auth/google", tags=["Autenticación"])

PROVIDER = "google"
STATE_COOKIE = "pokedex_oauth"
STATE_COOKIE_PATH = "/api/v1/auth/google"
STATE_TTL = timedelta(minutes=10)
STATE_AUDIENCE = "pokedex-oauth-state"
_ERROR_CODE = re.compile(r"^[a-z_]{1,50}$")

FLOW_EXPIRED = "El inicio de sesión con Google ha caducado o no es válido. Vuelve a empezar."


def get_google(request: Request) -> GoogleOAuthClient:
    google: GoogleOAuthClient = request.app.state.google
    if not google.enabled:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="El inicio de sesión con Google no está configurado en este servidor.",
        )
    return google


GoogleDep = Annotated[GoogleOAuthClient, Depends(get_google)]


# ═════════════════════════════════════════════════════════════
#  Cookie firmada con el estado del flujo
# ═════════════════════════════════════════════════════════════
def _encode_state(state: str, nonce: str, verifier: str) -> str:
    settings = get_settings()
    now = utc_now()
    return jwt.encode(
        {
            "state": state,
            "nonce": nonce,
            "verifier": verifier,
            "aud": STATE_AUDIENCE,  # no se puede confundir con un access token
            "iat": int(now.timestamp()),
            "exp": int((now + STATE_TTL).timestamp()),
        },
        settings.secret_key.get_secret_value(),
        algorithm=settings.jwt_algorithm,
    )


def _decode_state(cookie: str | None) -> dict:
    if not cookie:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=FLOW_EXPIRED)
    settings = get_settings()
    try:
        return jwt.decode(
            cookie,
            settings.secret_key.get_secret_value(),
            algorithms=[settings.jwt_algorithm],
            audience=STATE_AUDIENCE,
        )
    except JWTError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=FLOW_EXPIRED) from None


def _cookie_is_secure(request: Request) -> bool:
    return request.url.scheme == "https" or get_settings().is_production


# ═════════════════════════════════════════════════════════════
#  Usuarios
# ═════════════════════════════════════════════════════════════
def _username_for(session: SessionDep, identity: GoogleIdentity) -> str:
    """Nombre de usuario a partir del email: ``ash.ketchum@gmail.com`` → ``ash.ketchum``."""
    base = re.sub(r"[^a-z0-9._-]", "", identity.email.split("@")[0].lower())[:40]
    if len(base) < 3:
        base = f"trainer{base}"
    candidate = base
    while session.exec(select(User.id).where(User.username == candidate)).first() is not None:
        candidate = f"{base}-{secrets.token_hex(2)}"
    assert USERNAME_PATTERN.fullmatch(candidate)  # noqa: S101  (invariante interna)
    return candidate


def _resolve_user(session: SessionDep, identity: GoogleIdentity) -> tuple[User, bool]:
    """Usuario vinculado a esa cuenta de Google; si no hay, lo crea.

    Si ya existe una cuenta **local** con el mismo email, no se vincula automáticamente.
    Como los emails locales no se verifican, un atacante podría registrar antes el email de
    la víctima con su propia contraseña y, al vincularlo, compartirían la cuenta
    (*pre-account hijacking*). Se responde 409 y el usuario entra con su contraseña.
    """
    account = session.exec(
        select(OAuthAccount).where(
            OAuthAccount.provider == PROVIDER, OAuthAccount.subject == identity.subject
        )
    ).first()
    if account is not None:
        user = session.get(User, account.user_id)
        if user is None or not user.is_active:
            raise HTTPException(
                status.HTTP_401_UNAUTHORIZED, detail="Esta cuenta está desactivada."
            )
        return user, False

    if session.exec(select(User.id).where(User.email == identity.email)).first() is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail="Ya existe una cuenta con ese email. Inicia sesión con tu contraseña.",
        )

    user = User(
        username=_username_for(session, identity),
        email=identity.email,
        hashed_password=unusable_password(),
    )
    session.add(user)
    session.flush()
    session.add(
        OAuthAccount(
            user_id=user.id, provider=PROVIDER, subject=identity.subject, email=identity.email
        )
    )
    session.commit()
    session.refresh(user)
    return user, True


# ═════════════════════════════════════════════════════════════
#  Endpoints
# ═════════════════════════════════════════════════════════════
class GoogleStatus(BaseModel):
    enabled: bool


@router.get("/status", response_model=GoogleStatus, summary="¿Está disponible Google?")
@limiter.limit(rl.DEFAULT)
def google_status(request: Request, response: Response) -> GoogleStatus:
    """Indica si el servidor tiene configurado el inicio de sesión con Google, para que
    un cliente solo muestre el botón cuando funciona. No revela ninguna credencial."""
    return GoogleStatus(enabled=get_settings().google_enabled)


@router.get(
    "/login",
    summary="Iniciar sesión con Google",
    status_code=status.HTTP_302_FOUND,
    response_class=RedirectResponse,
    responses={
        302: {"description": "Redirección a Google"},
        503: {"description": "No configurado"},
    },
)
@limiter.limit(rl.LOGIN)
def google_login(request: Request, google: GoogleDep) -> RedirectResponse:
    """Abre esta URL **en el navegador** (no desde Swagger): redirige a la pantalla de
    Google para elegir cuenta y, al volver, entrega los tokens de la API."""
    state, nonce = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    verifier, challenge = new_pkce_pair()

    response = RedirectResponse(
        google.authorization_url(state=state, nonce=nonce, code_challenge=challenge),
        status_code=status.HTTP_302_FOUND,
    )
    response.set_cookie(
        STATE_COOKIE,
        _encode_state(state, nonce, verifier),
        max_age=int(STATE_TTL.total_seconds()),
        path=STATE_COOKIE_PATH,
        httponly=True,  # inaccesible desde JavaScript
        secure=_cookie_is_secure(request),
        samesite="lax",  # se envía en la redirección de vuelta desde Google, no en POST ajenos
    )
    return response


@router.get(
    "/callback",
    summary="Vuelta desde Google",
    # Solo la llama Google al terminar el login; probarla a mano desde /docs no tiene sentido.
    include_in_schema=False,
    responses={
        400: {"description": "Flujo caducado, manipulado o cancelado"},
        401: {"description": "Google no ha validado la identidad"},
        409: {"description": "Ya existe una cuenta local con ese email"},
        503: {"description": "No configurado"},
    },
)
@limiter.limit(rl.LOGIN)
async def google_callback(
    request: Request,
    session: SessionDep,
    google: GoogleDep,
    code: Annotated[str | None, Query(max_length=2048)] = None,
    state: Annotated[str | None, Query(max_length=256)] = None,
    error: Annotated[str | None, Query(max_length=100)] = None,
) -> Response:
    """Google redirige aquí. Devuelve los tokens de la API en JSON o, si está configurado
    `OAUTH_SUCCESS_REDIRECT`, redirige al frontend con los tokens en el fragmento de la
    URL (`#access_token=…`), que el navegador nunca envía a ningún servidor."""
    flow = _decode_state(request.cookies.get(STATE_COOKIE))

    if error is not None:
        reason = error if _ERROR_CODE.fullmatch(error) else "desconocido"
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail=f"Google ha cancelado el inicio de sesión ({reason}).",
        )
    if not state or not secrets.compare_digest(state, flow["state"]):
        logger.warning("Callback de Google con un state que no coincide")
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=FLOW_EXPIRED)
    if not code:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Falta el código de autorización.")

    try:
        tokens = await google.exchange_code(code, flow["verifier"])
        identity = await google.verify_id_token(
            tokens["id_token"], nonce=flow["nonce"], access_token=tokens.get("access_token")
        )
    except GoogleAuthError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc

    user, created = _resolve_user(session, identity)
    pair = issue_token_pair(session, user, family_id=uuid.uuid4().hex)
    logger.info("Login con Google: user_id=%s (cuenta nueva: %s)", user.id, created)

    success_url = get_settings().oauth_success_redirect
    response: Response
    if success_url:
        response = RedirectResponse(
            f"{success_url}#{urlencode(pair.model_dump())}", status_code=status.HTTP_302_FOUND
        )
    else:
        response = JSONResponse(pair.model_dump())
    response.headers["Cache-Control"] = "no-store"
    response.delete_cookie(STATE_COOKIE, path=STATE_COOKIE_PATH)
    return response
