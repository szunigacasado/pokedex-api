"""Sesión del navegador con el refresh token en una cookie HttpOnly (app/session_cookie.py).

Entrar desde /docs deja la sesión iniciada en la web, sin abrir la puerta al CSRF: la
cookie solo se emite y se acepta en peticiones del propio sitio."""

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.models import RefreshToken
from app.session_cookie import REFRESH_COOKIE

LOGIN = "/api/v1/auth/login"
REFRESH = "/api/v1/auth/refresh"
LOGOUT = "/api/v1/auth/logout"
SAME_ORIGIN = {"Origin": "http://testserver"}
EVIL_ORIGIN = {"Origin": "https://evil.example"}


def login(client: TestClient, user: dict, headers: dict | None = None):
    return client.post(
        LOGIN, data={"username": user["username"], "password": user["password"]}, headers=headers
    )


def set_cookie_header(response) -> str:
    return next(
        v for k, v in response.headers.multi_items() if k == "set-cookie" and REFRESH_COOKIE in v
    )


def test_login_from_the_site_sets_a_locked_down_cookie(client: TestClient, test_user: dict) -> None:
    response = login(client, test_user, SAME_ORIGIN)

    assert response.status_code == 200
    header = set_cookie_header(response).lower()
    assert "httponly" in header
    assert "samesite=strict" in header
    assert "path=/api/v1/auth" in header
    assert "max-age=604800" in header  # 7 días, como el refresh token
    assert response.json()["refresh_token"]  # los clientes de la API lo siguen recibiendo


def test_cookie_is_secure_over_https(test_user: dict, client: TestClient) -> None:
    secure_client = TestClient(client.app, base_url="https://testserver")
    response = login(secure_client, test_user, {"Origin": "https://testserver"})

    assert "secure" in set_cookie_header(response).lower()


def test_api_clients_get_no_cookie(client: TestClient, test_user: dict) -> None:
    """Sin Origin ni Sec-Fetch-Site (curl, Postman…): solo los tokens del cuerpo."""
    assert REFRESH_COOKIE not in login(client, test_user).headers.get("set-cookie", "")


def test_another_site_cannot_log_you_in(client: TestClient, test_user: dict) -> None:
    """Login CSRF: un formulario de otra web no deja su sesión en tu navegador."""
    assert REFRESH_COOKIE not in login(client, test_user, EVIL_ORIGIN).headers.get("set-cookie", "")


def test_the_web_renews_with_the_cookie_alone(client: TestClient, test_user: dict) -> None:
    login(client, test_user, SAME_ORIGIN)
    first = client.cookies.get(REFRESH_COOKIE)

    response = client.post(REFRESH, headers=SAME_ORIGIN)

    assert response.status_code == 200
    assert client.cookies.get(REFRESH_COOKIE) not in (None, first)  # rotada


def test_fetch_metadata_is_accepted_without_origin(client: TestClient, test_user: dict) -> None:
    login(client, test_user, SAME_ORIGIN)

    assert client.post(REFRESH, headers={"Sec-Fetch-Site": "same-origin"}).status_code == 200


def test_the_cookie_is_ignored_from_another_site(client: TestClient, test_user: dict) -> None:
    login(client, test_user, SAME_ORIGIN)

    assert client.post(REFRESH, headers=EVIL_ORIGIN).status_code == 401
    assert client.post(REFRESH, headers={"Sec-Fetch-Site": "cross-site"}).status_code == 401
    assert client.post(REFRESH).status_code == 401  # sin ninguna cabecera de origen


def test_reusing_a_rotated_cookie_revokes_the_session(
    client: TestClient, session: Session, test_user: dict
) -> None:
    login(client, test_user, SAME_ORIGIN)
    stolen = client.cookies.get(REFRESH_COOKIE)
    client.post(REFRESH, headers=SAME_ORIGIN)

    client.cookies.set(REFRESH_COOKIE, stolen, path="/api/v1/auth")
    assert client.post(REFRESH, headers=SAME_ORIGIN).status_code == 401
    assert all(t.revoked_at is not None for t in session.exec(select(RefreshToken)).all())


def test_logout_clears_the_cookie_and_the_session(client: TestClient, test_user: dict) -> None:
    login(client, test_user, SAME_ORIGIN)
    cookie = client.cookies.get(REFRESH_COOKIE)

    response = client.post(LOGOUT, headers=SAME_ORIGIN)

    assert response.status_code == 204
    assert (
        "max-age=0" in set_cookie_header(response).lower()
        or "expires=" in set_cookie_header(response).lower()
    )
    client.cookies.set(REFRESH_COOKIE, cookie, path="/api/v1/auth")
    assert client.post(REFRESH, headers=SAME_ORIGIN).status_code == 401


def test_no_body_and_no_cookie_is_401(client: TestClient) -> None:
    assert client.post(REFRESH, headers=SAME_ORIGIN).status_code == 401
    assert client.post(LOGOUT, headers=SAME_ORIGIN).status_code == 401


def test_body_still_wins_for_api_clients(client: TestClient, test_user: dict) -> None:
    tokens = login(client, test_user).json()

    response = client.post(REFRESH, json={"refresh_token": tokens["refresh_token"]})

    assert response.status_code == 200
    assert response.json()["refresh_token"] != tokens["refresh_token"]
