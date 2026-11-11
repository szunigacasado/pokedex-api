"""Tests del inicio de sesión con Google.

Google se simula por completo: el test genera un par de claves RSA, publica la clave
pública como JWKS y firma los ID tokens con la privada. Así se prueba la verificación
real de la firma sin depender de Google ni de la red.
"""

import base64
import hashlib
import time
from collections.abc import Iterator
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
import respx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from jose import jwk, jwt
from sqlmodel import Session, select

from app.config import get_settings
from app.models import OAuthAccount, User
from app.routers.oauth import STATE_AUDIENCE, STATE_COOKIE
from app.services.google_oauth import JWKS_URL, TOKEN_URL

LOGIN = "/api/v1/auth/google/login"
CALLBACK = "/api/v1/auth/google/callback"
CLIENT_ID = "pokedex-test.apps.googleusercontent.com"


# ═════════════════════════════════════════════════════════════
#  Google simulado
# ═════════════════════════════════════════════════════════════
def _keypair(kid: str) -> tuple[bytes, dict]:
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = private.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )
    public = private.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    return pem, jwk.construct(public, "RS256").to_dict() | {"kid": kid, "use": "sig"}


GOOGLE_KEY, GOOGLE_JWK = _keypair("google-1")
ATTACKER_KEY, _ = _keypair("google-1")  # mismo kid, otra clave


def id_token(nonce: str, *, key: bytes = GOOGLE_KEY, kid: str = "google-1", **overrides) -> str:
    now = int(time.time())
    claims = {
        "iss": "https://accounts.google.com",
        "aud": CLIENT_ID,
        "sub": "1234567890",
        "email": "Ash.Ketchum@gmail.com",
        "email_verified": True,
        "name": "Ash Ketchum",
        "iat": now,
        "exp": now + 300,
        "nonce": nonce,
    } | overrides
    return jwt.encode(claims, key, algorithm="RS256", headers={"kid": kid}, access_token="at-123")


@pytest.fixture
def google_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "google_client_id", CLIENT_ID)
    monkeypatch.setattr(settings, "google_client_secret", get_settings().secret_key)


@pytest.fixture
def google(google_enabled) -> Iterator[respx.MockRouter]:
    with respx.mock(assert_all_called=False) as router:
        router.get(JWKS_URL).respond(json={"keys": [GOOGLE_JWK]})
        yield router


def start_login(client: TestClient) -> dict[str, str]:
    """Primer paso: devuelve los parámetros con los que se redirige a Google."""
    response = client.get(LOGIN, follow_redirects=False)
    assert response.status_code == 302, response.text
    return {k: v[0] for k, v in parse_qs(urlparse(response.headers["location"]).query).items()}


def google_returns(router: respx.MockRouter, token: str | None, status: int = 200) -> respx.Route:
    if status != 200:
        return router.post(TOKEN_URL).respond(status, json={"error": "invalid_grant"})
    body = {"id_token": token, "access_token": "at-123"} if token else {"access_token": "at"}
    return router.post(TOKEN_URL).respond(200, json=body)


def complete_login(
    client: TestClient, router: respx.MockRouter, *, follow_redirects: bool = True, **overrides
):
    params = start_login(client)
    nonce = overrides.pop("nonce", params["nonce"])
    google_returns(router, id_token(nonce, **overrides))
    return client.get(
        CALLBACK,
        params={"code": "codigo-de-google", "state": params["state"]},
        follow_redirects=follow_redirects,
    )


# ═════════════════════════════════════════════════════════════
#  Inicio del flujo
# ═════════════════════════════════════════════════════════════
def test_google_login_is_disabled_without_credentials(client: TestClient) -> None:
    for path in (LOGIN, CALLBACK):
        response = client.get(path, follow_redirects=False)
        assert response.status_code == 503
        assert "no está configurado" in response.json()["detail"]


def test_login_redirects_to_google_with_state_nonce_and_pkce(client: TestClient, google) -> None:
    params = start_login(client)

    assert params["client_id"] == CLIENT_ID
    assert params["response_type"] == "code"
    assert params["scope"] == "openid email profile"
    assert params["redirect_uri"] == get_settings().google_redirect_uri
    assert params["code_challenge_method"] == "S256"
    assert len(params["state"]) >= 32 and len(params["nonce"]) >= 32

    flow = jwt.get_unverified_claims(client.cookies[STATE_COOKIE])
    expected = base64.urlsafe_b64encode(hashlib.sha256(flow["verifier"].encode()).digest())
    assert params["code_challenge"] == expected.decode().rstrip("=")
    assert flow["state"] == params["state"] and flow["nonce"] == params["nonce"]


def test_state_cookie_is_protected(client: TestClient, google) -> None:
    cookie = client.get(LOGIN, follow_redirects=False).headers["set-cookie"].lower()

    assert "httponly" in cookie
    assert "samesite=lax" in cookie
    assert "path=/api/v1/auth/google" in cookie
    assert "max-age=600" in cookie
    assert "secure" not in cookie  # en local por HTTP

    https = TestClient(client.app, base_url="https://testserver")
    assert "secure" in https.get(LOGIN, follow_redirects=False).headers["set-cookie"].lower()


# ═════════════════════════════════════════════════════════════
#  Callback correcto
# ═════════════════════════════════════════════════════════════
def test_first_google_login_creates_the_account(
    client: TestClient, google, session: Session
) -> None:
    response = complete_login(client, google)

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    tokens = response.json()
    me = client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {tokens['access_token']}"}
    )
    assert me.json()["username"] == "ash.ketchum"
    assert me.json()["email"] == "ash.ketchum@gmail.com"

    account = session.exec(select(OAuthAccount)).one()
    assert (account.provider, account.subject) == ("google", "1234567890")
    assert STATE_COOKIE not in client.cookies  # la cookie temporal se borra


def test_code_verifier_is_sent_to_google(client: TestClient, google) -> None:
    params = start_login(client)
    route = google_returns(google, id_token(params["nonce"]))
    verifier = jwt.get_unverified_claims(client.cookies[STATE_COOKIE])["verifier"]

    client.get(CALLBACK, params={"code": "c", "state": params["state"]})

    sent = parse_qs(route.calls.last.request.content.decode())
    assert sent["code_verifier"] == [verifier]
    assert sent["grant_type"] == ["authorization_code"]


def test_second_login_reuses_the_account(client: TestClient, google, session: Session) -> None:
    complete_login(client, google)
    complete_login(client, google, email="nuevo-email@gmail.com")  # el email puede cambiar

    assert len(session.exec(select(User)).all()) == 1
    assert len(session.exec(select(OAuthAccount)).all()) == 1


def test_redirects_to_the_frontend_with_tokens_in_the_fragment(
    client: TestClient, google, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "oauth_success_redirect", "http://localhost:5173/login")

    response = complete_login(client, google, follow_redirects=False)

    assert response.status_code == 302
    location = response.headers["location"]
    assert location.startswith("http://localhost:5173/login#access_token=")
    assert "refresh_token=" in location and "?" not in location  # nunca en la query


def test_username_collisions_get_a_suffix(client: TestClient, google, session: Session) -> None:
    session.add(User(username="ash.ketchum", email="otro@kanto.dev", hashed_password="x"))
    session.commit()

    complete_login(client, google)

    created = session.exec(select(User).where(User.email == "ash.ketchum@gmail.com")).one()
    assert created.username.startswith("ash.ketchum-")


def test_short_email_names_are_padded(client: TestClient, google, session: Session) -> None:
    complete_login(client, google, email="Al@gmail.com", sub="999")

    assert session.exec(select(User.username)).one() == "traineral"


# ═════════════════════════════════════════════════════════════
#  Ataques y errores
# ═════════════════════════════════════════════════════════════
@pytest.mark.parametrize(
    "overrides",
    [
        {"key": ATTACKER_KEY},  # firmado por otro
        {"aud": "otra-aplicacion.apps.googleusercontent.com"},
        {"iss": "https://accounts.evil.example"},
        {"exp": int(time.time()) - 10},
        {"nonce": "nonce-de-otro-login"},
        {"email_verified": False},
    ],
)
def test_invalid_id_tokens_are_rejected(
    client: TestClient, google, session: Session, overrides: dict
) -> None:
    response = complete_login(client, google, **overrides)

    assert response.status_code == 401
    assert session.exec(select(User)).all() == []


def test_existing_local_account_is_not_hijacked(
    client: TestClient, google, session: Session
) -> None:
    """Pre-secuestro: alguien registró antes el email de la víctima con su contraseña."""
    session.add(User(username="atacante", email="ash.ketchum@gmail.com", hashed_password="x"))
    session.commit()

    response = complete_login(client, google)

    assert response.status_code == 409
    assert session.exec(select(OAuthAccount)).all() == []


def test_state_mismatch_is_rejected(client: TestClient, google) -> None:
    params = start_login(client)
    google_returns(google, id_token(params["nonce"]))

    response = client.get(CALLBACK, params={"code": "c", "state": "state-de-un-atacante"})

    assert response.status_code == 400


def test_callback_without_the_login_cookie_is_rejected(client: TestClient, google) -> None:
    """CSRF de login: el atacante envía a la víctima un callback de su propio login."""
    response = client.get(CALLBACK, params={"code": "c", "state": "x"})

    assert response.status_code == 400
    assert "caducado" in response.json()["detail"]


def test_tampered_or_expired_cookie_is_rejected(client: TestClient, google) -> None:
    settings = get_settings()
    now = int(time.time())
    expired = jwt.encode(
        {"state": "s", "nonce": "n", "verifier": "v", "aud": STATE_AUDIENCE, "exp": now - 1},
        settings.secret_key.get_secret_value(),
        algorithm="HS256",
    )
    forged = jwt.encode(
        {"state": "s", "nonce": "n", "verifier": "v", "aud": STATE_AUDIENCE, "exp": now + 60},
        "clave-del-atacante-0123456789abcdef",
        algorithm="HS256",
    )
    for cookie in (expired, forged, "basura"):
        client.cookies.set(STATE_COOKIE, cookie, path="/api/v1/auth/google")
        assert client.get(CALLBACK, params={"code": "c", "state": "s"}).status_code == 400


def test_user_cancelling_at_google(client: TestClient, google) -> None:
    params = start_login(client)

    response = client.get(CALLBACK, params={"error": "access_denied", "state": params["state"]})
    weird = client.get(CALLBACK, params={"error": "<script>", "state": params["state"]})

    assert response.json()["detail"] == "Google ha cancelado el inicio de sesión (access_denied)."
    assert "desconocido" in weird.json()["detail"]


def test_missing_code(client: TestClient, google) -> None:
    params = start_login(client)

    assert client.get(CALLBACK, params={"state": params["state"]}).status_code == 400


@pytest.mark.parametrize(
    "token_response",
    [
        {"status": 400},  # Google rechaza el código
        {"status": 200, "missing_id_token": True},
    ],
)
def test_failed_code_exchange(client: TestClient, google, token_response: dict) -> None:
    params = start_login(client)
    google_returns(
        google,
        None if token_response.get("missing_id_token") else id_token(params["nonce"]),
        status=token_response["status"],
    )

    response = client.get(CALLBACK, params={"code": "c", "state": params["state"]})

    assert response.status_code == 401


def test_google_unreachable(client: TestClient, google) -> None:
    params = start_login(client)
    google.post(TOKEN_URL).mock(side_effect=httpx.ConnectError("sin red"))

    response = client.get(CALLBACK, params={"code": "c", "state": params["state"]})

    assert response.status_code == 401
    assert response.json()["detail"] == "No se ha podido contactar con Google."


def test_malformed_id_token(client: TestClient, google) -> None:
    params = start_login(client)
    google_returns(google, "esto-no-es-un-jwt")

    assert client.get(CALLBACK, params={"code": "c", "state": params["state"]}).status_code == 401


def test_jwks_unavailable(client: TestClient, google_enabled) -> None:
    with respx.mock(assert_all_called=False) as router:
        router.get(JWKS_URL).respond(500)
        response = complete_login(client, router)

    assert response.status_code == 401


def test_google_key_rotation(client: TestClient, google_enabled) -> None:
    """Si el kid no está en el JWKS en caché, se recarga una vez."""
    new_key, new_jwk = _keypair("google-2")
    with respx.mock(assert_all_called=False) as router:
        jwks = router.get(JWKS_URL).mock(
            side_effect=[
                httpx.Response(200, json={"keys": [GOOGLE_JWK]}),
                httpx.Response(200, json={"keys": [GOOGLE_JWK, new_jwk]}),
            ]
        )
        response = complete_login(client, router, key=new_key, kid="google-2")

    assert response.status_code == 200
    assert jwks.call_count == 2


def test_jwks_is_cached(client: TestClient, google) -> None:
    complete_login(client, google)
    complete_login(client, google)

    assert google.routes[0].call_count == 1


def test_disabled_linked_user_cannot_log_in(client: TestClient, google, session: Session) -> None:
    complete_login(client, google)
    user = session.exec(select(User)).one()
    user.is_active = False
    session.add(user)
    session.commit()

    assert complete_login(client, google).status_code == 401


def test_google_users_cannot_log_in_with_a_password(
    client: TestClient, google, session: Session
) -> None:
    complete_login(client, google)

    response = client.post(
        "/api/v1/auth/login", data={"username": "ash.ketchum", "password": "Cualquiera123"}
    )

    assert response.status_code == 401
    assert session.exec(select(User)).one().hashed_password.startswith("!oauth$")


# ═════════════════════════════════════════════════════════════
#  Swagger
# ═════════════════════════════════════════════════════════════
def test_swagger_offers_a_bearer_field(client: TestClient) -> None:
    schemes = client.get("/openapi.json").json()["components"]["securitySchemes"]

    assert schemes["HTTPBearer"]["scheme"] == "bearer"
    assert "OAuth2PasswordBearer" in schemes
