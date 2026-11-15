"""Tests de autenticación: registro, login, protección de endpoints y tokens."""

import base64
import json
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from jose import jwt
from passlib.context import CryptContext
from sqlmodel import Session, select

from app.auth import password_needs_rehash, verify_password
from app.config import get_settings
from app.models import LoginThrottle, RefreshToken, User, utc_now
from app.rate_limit import limiter

REGISTER = "/api/v1/auth/register"
LOGIN = "/api/v1/auth/login"
REFRESH = "/api/v1/auth/refresh"
LOGOUT = "/api/v1/auth/logout"
ME = "/api/v1/auth/me"


def login(client: TestClient, username: str, password: str):
    return client.post(LOGIN, data={"username": username, "password": password})


def forge_token(**overrides) -> str:
    """Crea un JWT firmado con la clave real, con los claims que se quieran alterar."""
    settings = get_settings()
    now = utc_now()
    claims = {
        "sub": "ash",
        "user_id": 1,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(hours=1)).timestamp()),
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
        "jti": "jti-de-prueba",
        "type": "access",
    } | overrides
    return jwt.encode(claims, settings.secret_key.get_secret_value(), algorithm="HS256")


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# ═════════════════════════════════════════════════════════════
#  Registro
# ═════════════════════════════════════════════════════════════
def test_password_is_stored_hashed_with_bcrypt(
    client: TestClient, session: Session, test_user: dict
) -> None:
    user = session.exec(select(User).where(User.username == "ash")).one()

    assert user.hashed_password != test_user["password"]
    assert user.hashed_password.startswith("$2b$")
    assert verify_password(test_user["password"], user.hashed_password)


def test_register_duplicate_email(client: TestClient, test_user: dict) -> None:
    response = client.post(
        REGISTER, json={"username": "otro", "email": "ASH@kanto.dev", "password": "Pikachu123"}
    )

    assert response.status_code == 409


@pytest.mark.parametrize(
    "password",
    [
        "Corta1A",  # menos de 8 caracteres
        "sinmayuscula1",  # sin mayúscula
        "SinNumeroNinguno",  # sin número
        "MiAsh1234",  # contiene el nombre de usuario
        "Ñ1" + "ñ" * 40,  # 41 caracteres pero 83 bytes: supera el límite de bcrypt
    ],
)
def test_register_rejects_weak_passwords(client: TestClient, password: str) -> None:
    response = client.post(
        REGISTER, json={"username": "ash", "email": "ash@kanto.dev", "password": password}
    )

    assert response.status_code == 422


@pytest.mark.parametrize("email", ["sin-arroba.dev", "a@b", "a@@b.dev", "espacio @b.dev", "a@b.c"])
def test_register_rejects_invalid_emails(client: TestClient, email: str) -> None:
    response = client.post(
        REGISTER, json={"username": "ash", "email": email, "password": "Pikachu123"}
    )

    assert response.status_code == 422


@pytest.mark.parametrize("username", ["ab", "con espacio", "emoji😀", "x" * 51])
def test_register_rejects_invalid_usernames(client: TestClient, username: str) -> None:
    response = client.post(
        REGISTER, json={"username": username, "email": "ash@kanto.dev", "password": "Pikachu123"}
    )

    assert response.status_code == 422


# ═════════════════════════════════════════════════════════════
#  Login
# ═════════════════════════════════════════════════════════════
def test_login_accepts_email_as_username(client: TestClient, test_user: dict) -> None:
    assert login(client, "ASH@kanto.dev", test_user["password"]).status_code == 200


def test_login_unknown_user_gives_the_same_answer(client: TestClient, test_user: dict) -> None:
    wrong_password = login(client, "ash", "Incorrecta123")
    unknown_user = login(client, "gary", "Incorrecta123")

    assert unknown_user.status_code == wrong_password.status_code == 401
    assert unknown_user.json() == wrong_password.json()


def test_account_is_locked_after_repeated_failures(client: TestClient, test_user: dict) -> None:
    for _ in range(get_settings().max_failed_logins):
        assert login(client, "ash", "Incorrecta123").status_code == 401

    # Bloqueada: ni siquiera la contraseña correcta funciona hasta que pase el tiempo.
    response = login(client, "ash", test_user["password"])

    assert response.status_code == 429
    assert 0 < int(response.headers["retry-after"]) <= get_settings().lockout_minutes * 60


def test_lock_expires(client: TestClient, session: Session, test_user: dict) -> None:
    for _ in range(get_settings().max_failed_logins):
        login(client, "ash", "Incorrecta123")
    throttle = session.exec(select(LoginThrottle)).one()
    throttle.locked_until = utc_now() - timedelta(seconds=1)
    session.add(throttle)
    session.commit()

    assert login(client, "ash", test_user["password"]).status_code == 200


def test_successful_login_clears_the_failed_attempts(
    client: TestClient, session: Session, test_user: dict
) -> None:
    login(client, "ash", "Incorrecta123")
    assert session.exec(select(LoginThrottle)).one().failures == 1

    login(client, "ash", test_user["password"])

    session.expire_all()
    assert session.exec(select(LoginThrottle)).all() == []


def test_the_lock_only_affects_the_ip_that_failed(
    client: TestClient, session: Session, test_user: dict
) -> None:
    """Cinco contraseñas falsas desde otra IP no dejan al dueño sin acceso desde la suya."""
    session.add(
        LoginThrottle(
            identifier="ash", ip="203.0.113.9", locked_until=utc_now() + timedelta(minutes=15)
        )
    )
    session.commit()

    assert login(client, "ash", test_user["password"]).status_code == 200


def test_unknown_users_are_locked_the_same_way(client: TestClient, test_user: dict) -> None:
    """El bloqueo no revela qué cuentas existen."""
    responses = {}
    for name in ("ash", "nadie"):
        limiter.reset()  # el límite de 10 logins por minuto y por IP es otra protección
        for _ in range(get_settings().max_failed_logins):
            assert login(client, name, "Incorrecta123").status_code == 401
        responses[name] = login(client, name, "Incorrecta123")

    assert responses["ash"].status_code == responses["nadie"].status_code == 429
    assert responses["ash"].json() == responses["nadie"].json()


def test_logout_with_an_invalid_token_still_clears_the_cookie(client: TestClient) -> None:
    response = client.post("/api/v1/auth/logout", json={"refresh_token": "no-es-un-token"})

    assert response.status_code == 401
    assert "pokedex_refresh=" in response.headers["set-cookie"]
    assert "Max-Age=0" in response.headers["set-cookie"]


def test_inactive_user_cannot_login(client: TestClient, session: Session, test_user: dict) -> None:
    user = session.exec(select(User).where(User.username == "ash")).one()
    user.is_active = False
    session.add(user)
    session.commit()

    assert login(client, "ash", test_user["password"]).status_code == 401


def test_login_upgrades_outdated_password_hash(client: TestClient, session: Session) -> None:
    old_hash = CryptContext(schemes=["bcrypt"], bcrypt__rounds=5).hash("Charizard99")
    session.add(User(username="red", email="red@kanto.dev", hashed_password=old_hash))
    session.commit()
    assert password_needs_rehash(old_hash)

    assert login(client, "red", "Charizard99").status_code == 200

    session.expire_all()
    new_hash = session.exec(select(User).where(User.username == "red")).one().hashed_password
    assert new_hash != old_hash
    assert not password_needs_rehash(new_hash)


# ═════════════════════════════════════════════════════════════
#  Endpoints protegidos
# ═════════════════════════════════════════════════════════════
def test_access_protected_endpoint_with_valid_token(
    client: TestClient, auth_headers: dict, test_user: dict
) -> None:
    response = client.get(ME, headers=auth_headers)

    assert response.status_code == 200
    assert response.json()["id"] == test_user["id"]


def test_expired_token_is_rejected(client: TestClient, test_user: dict) -> None:
    past = utc_now() - timedelta(hours=2)
    token = forge_token(
        user_id=test_user["id"],
        iat=int(past.timestamp()),
        exp=int((past + timedelta(hours=1)).timestamp()),
    )

    assert client.get(ME, headers=bearer(token)).status_code == 401


def test_token_with_wrong_signature_is_rejected(client: TestClient, test_user: dict) -> None:
    token = jwt.encode(
        {"sub": "ash", "user_id": test_user["id"], "type": "access"},
        "otra-clave-que-no-es-la-del-servidor-1234567890",
        algorithm="HS256",
    )

    assert client.get(ME, headers=bearer(token)).status_code == 401


def test_unsigned_token_alg_none_is_rejected(client: TestClient, test_user: dict) -> None:
    def b64(data: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(data).encode()).rstrip(b"=").decode()

    claims = jwt.get_unverified_claims(forge_token(user_id=test_user["id"]))
    unsigned = f"{b64({'alg': 'none', 'typ': 'JWT'})}.{b64(claims)}."

    assert client.get(ME, headers=bearer(unsigned)).status_code == 401


def test_token_without_numeric_user_id_is_rejected(client: TestClient, test_user: dict) -> None:
    token = forge_token(user_id=str(test_user["id"]))

    assert client.get(ME, headers=bearer(token)).status_code == 401


def test_token_for_another_audience_is_rejected(client: TestClient, test_user: dict) -> None:
    token = forge_token(user_id=test_user["id"], aud="otra-aplicacion")

    assert client.get(ME, headers=bearer(token)).status_code == 401


def test_refresh_token_cannot_be_used_as_access_token(client: TestClient, tokens: dict) -> None:
    assert client.get(ME, headers=bearer(tokens["refresh_token"])).status_code == 401


def test_token_of_deleted_user_is_rejected(
    client: TestClient, session: Session, auth_headers: dict
) -> None:
    session.delete(session.exec(select(User).where(User.username == "ash")).one())
    session.commit()

    assert client.get(ME, headers=auth_headers).status_code == 401


# ═════════════════════════════════════════════════════════════
#  Refresh tokens y logout
# ═════════════════════════════════════════════════════════════
def test_refresh_returns_a_new_token_pair(client: TestClient, tokens: dict) -> None:
    response = client.post(REFRESH, json={"refresh_token": tokens["refresh_token"]})

    assert response.status_code == 200
    new_tokens = response.json()
    assert new_tokens["refresh_token"] != tokens["refresh_token"]
    assert client.get(ME, headers=bearer(new_tokens["access_token"])).status_code == 200


def test_refresh_token_lasts_seven_days(tokens: dict) -> None:
    claims = jwt.get_unverified_claims(tokens["refresh_token"])

    assert claims["exp"] - claims["iat"] == 7 * 24 * 60 * 60


def test_reused_refresh_token_revokes_the_whole_session(client: TestClient, tokens: dict) -> None:
    rotated = client.post(REFRESH, json={"refresh_token": tokens["refresh_token"]}).json()

    # Alguien reutiliza el token antiguo (por ejemplo, un atacante que lo ha robado)...
    reuse = client.post(REFRESH, json={"refresh_token": tokens["refresh_token"]})
    assert reuse.status_code == 401

    # ...y como medida de seguridad también deja de valer el token legítimo más reciente.
    legit = client.post(REFRESH, json={"refresh_token": rotated["refresh_token"]})
    assert legit.status_code == 401


def test_access_token_cannot_be_used_to_refresh(client: TestClient, tokens: dict) -> None:
    response = client.post(REFRESH, json={"refresh_token": tokens["access_token"]})

    assert response.status_code == 401


def test_unknown_refresh_token_is_rejected(client: TestClient, test_user: dict) -> None:
    token = forge_token(user_id=test_user["id"], type="refresh", jti="no-existe")

    assert client.post(REFRESH, json={"refresh_token": token}).status_code == 401


def test_refresh_for_inactive_user_is_rejected(
    client: TestClient, session: Session, tokens: dict
) -> None:
    user = session.exec(select(User).where(User.username == "ash")).one()
    user.is_active = False
    session.add(user)
    session.commit()

    assert client.post(REFRESH, json={"refresh_token": tokens["refresh_token"]}).status_code == 401


def test_logout_revokes_the_session(client: TestClient, session: Session, tokens: dict) -> None:
    response = client.post(LOGOUT, json={"refresh_token": tokens["refresh_token"]})

    assert response.status_code == 204
    assert client.post(REFRESH, json={"refresh_token": tokens["refresh_token"]}).status_code == 401
    assert all(t.revoked_at is not None for t in session.exec(select(RefreshToken)).all())


def test_logout_with_invalid_token(client: TestClient) -> None:
    assert client.post(LOGOUT, json={"refresh_token": "no-es-un-jwt"}).status_code == 401


# ═════════════════════════════════════════════════════════════
#  Utilidades
# ═════════════════════════════════════════════════════════════
def test_verify_password_without_user_spends_time_and_fails() -> None:
    assert verify_password("Pikachu123", None) is False
