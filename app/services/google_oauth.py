"""Cliente de OpenID Connect para iniciar sesión con Google.

Implementa el flujo *authorization code* con las protecciones que recomiendan el RFC 9700
(*OAuth 2.0 Security Best Current Practice*) y OpenID Connect:

- **state**: valor aleatorio que vuelve en el callback. Impide el CSRF de login, es decir,
  que un atacante haga que la víctima termine con la sesión del atacante.
- **PKCE** (S256): aunque alguien interceptara el código de autorización, no podría
  canjearlo sin el ``code_verifier``, que nunca sale de nuestro servidor.
- **nonce**: viaja dentro del ID token firmado. Impide reutilizar un ID token antiguo.
- **Verificación completa del ID token**: firma RS256 con las claves públicas de Google
  (JWKS), emisor, audiencia (nuestro client_id), caducidad, ``nonce``, ``at_hash`` y que
  el email esté verificado. Nunca se confía en un token sin verificarlo.

No se usa ninguna librería OAuth adicional: ``httpx`` para hablar con Google y
``python-jose`` (la del enunciado) para verificar la firma.
"""

import base64
import hashlib
import logging
import secrets
import time
from dataclasses import dataclass
from typing import Any, Final
from urllib.parse import urlencode

import httpx
from jose import JWTError, jwt

from app.config import Settings, get_settings
from app.services.resilience import Clock

logger = logging.getLogger("pokedex_api.oauth")

AUTHORIZATION_URL: Final = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL: Final = "https://oauth2.googleapis.com/token"  # noqa: S105  (URL, no una clave)
JWKS_URL: Final = "https://www.googleapis.com/oauth2/v3/certs"
ISSUERS: Final = ("https://accounts.google.com", "accounts.google.com")
JWKS_TTL_SECONDS: Final = 3600


class GoogleAuthError(Exception):
    """El inicio de sesión con Google no se ha podido completar."""


@dataclass(frozen=True, slots=True)
class GoogleIdentity:
    subject: str
    email: str
    name: str | None


def new_pkce_pair() -> tuple[str, str]:
    """Devuelve ``(code_verifier, code_challenge)`` según el RFC 7636 (método S256)."""
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    return verifier, challenge


class GoogleOAuthClient:
    def __init__(
        self,
        settings: Settings | None = None,
        client: httpx.AsyncClient | None = None,
        *,
        clock: Clock = time.monotonic,
    ) -> None:
        self._settings = settings or get_settings()
        self._client = client or httpx.AsyncClient(timeout=10.0)
        self._clock = clock
        self._jwks: dict[str, Any] | None = None
        self._jwks_expires_at = 0.0

    @property
    def enabled(self) -> bool:
        return self._settings.google_enabled

    async def close(self) -> None:
        await self._client.aclose()

    def authorization_url(self, *, state: str, nonce: str, code_challenge: str) -> str:
        params = {
            "client_id": self._settings.google_client_id,
            "redirect_uri": self._settings.google_redirect_uri,
            "response_type": "code",
            "scope": "openid email profile",
            "state": state,
            "nonce": nonce,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
            "prompt": "select_account",
        }
        return f"{AUTHORIZATION_URL}?{urlencode(params)}"

    async def exchange_code(self, code: str, code_verifier: str) -> dict[str, Any]:
        """Canjea el código de autorización por los tokens de Google."""
        secret = self._settings.google_client_secret
        try:
            response = await self._client.post(
                TOKEN_URL,
                data={
                    "code": code,
                    "client_id": self._settings.google_client_id,
                    "client_secret": secret.get_secret_value() if secret else "",
                    "redirect_uri": self._settings.google_redirect_uri,
                    "grant_type": "authorization_code",
                    "code_verifier": code_verifier,
                },
                headers={"Accept": "application/json"},
            )
        except httpx.HTTPError as exc:
            raise GoogleAuthError("No se ha podido contactar con Google.") from exc

        if response.status_code != 200:
            # Se registra el código de error de Google, nunca el cuerpo completo.
            error = response.json().get("error", "desconocido") if response.content else "vacío"
            logger.warning("Google rechazó el canje del código: %s %s", response.status_code, error)
            raise GoogleAuthError("Google no ha aceptado el código de autorización.")
        tokens = response.json()
        if "id_token" not in tokens:
            raise GoogleAuthError("La respuesta de Google no incluye un ID token.")
        return tokens

    async def _get_jwks(self, *, force: bool = False) -> dict[str, Any]:
        if not force and self._jwks is not None and self._clock() < self._jwks_expires_at:
            return self._jwks
        try:
            response = await self._client.get(JWKS_URL)
            response.raise_for_status()
            jwks = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise GoogleAuthError(
                "No se han podido obtener las claves públicas de Google."
            ) from exc
        self._jwks, self._jwks_expires_at = jwks, self._clock() + JWKS_TTL_SECONDS
        return jwks

    async def verify_id_token(
        self, id_token: str, *, nonce: str, access_token: str | None = None
    ) -> GoogleIdentity:
        """Verifica el ID token y devuelve la identidad, o lanza ``GoogleAuthError``."""
        try:
            kid = jwt.get_unverified_header(id_token).get("kid")
        except JWTError as exc:
            raise GoogleAuthError("ID token mal formado.") from exc

        jwks = await self._get_jwks()
        if kid not in {key.get("kid") for key in jwks.get("keys", [])}:
            # Google rota sus claves: si no está el kid, se recarga el JWKS una vez.
            jwks = await self._get_jwks(force=True)

        try:
            claims = jwt.decode(
                id_token,
                jwks,
                algorithms=["RS256"],  # nunca se acepta el algoritmo que diga el token
                audience=self._settings.google_client_id,
                issuer=list(ISSUERS),
                access_token=access_token,
                options={"require_exp": True, "require_iat": True, "require_sub": True},
            )
        except JWTError as exc:
            logger.warning("ID token de Google rechazado: %s", exc.__class__.__name__)
            raise GoogleAuthError("El ID token de Google no es válido.") from exc

        if not secrets.compare_digest(str(claims.get("nonce", "")), nonce):
            raise GoogleAuthError("El nonce del ID token no coincide.")
        if claims.get("email_verified") is not True or not claims.get("email"):
            raise GoogleAuthError("La cuenta de Google no tiene un email verificado.")

        return GoogleIdentity(
            subject=str(claims["sub"]),
            email=str(claims["email"]).lower(),
            name=claims.get("name"),
        )
