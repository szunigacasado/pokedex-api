"""Los tests que pide el enunciado (apartado 3.1), reunidos en un fichero.

Tienen los nombres exactos del enunciado y solo usan los fixtures comunes de
``conftest.py``. El resto de la suite (``tests/test_*.py``) prueba cada módulo en detalle.
"""

import httpx
import pytest
import respx
from fastapi.testclient import TestClient
from jose import jwt
from sqlmodel import Session, select

from app.config import get_settings
from app.models import User
from app.services.pokeapi_service import PokeAPIService, PokemonNotFoundError
from tests.fakes import BASE, pokemon_payload

REGISTER = "/api/v1/auth/register"
LOGIN = "/api/v1/auth/login"
ME = "/api/v1/auth/me"
POKEDEX = "/api/v1/pokedex"


def login(client: TestClient, username: str, password: str) -> httpx.Response:
    return client.post(LOGIN, data={"username": username, "password": password})


@pytest.fixture
def add(client: TestClient, auth_headers: dict, api):
    """Añade un Pokémon a la Pokédex de ash. PokeAPI simulada: 4, 37 y 58 (conftest)."""

    def _add(pokemon_id: int, expected: int = 201, **fields) -> dict:
        response = client.post(
            POKEDEX, json={"pokemon_id": pokemon_id, **fields}, headers=auth_headers
        )
        assert response.status_code == expected, response.text
        return response.json()

    return _add


@pytest.fixture
def other_user_entry(client: TestClient, api) -> dict:
    """Una entrada de la Pokédex de otro entrenador (gary)."""
    client.post(
        REGISTER, json={"username": "gary", "email": "gary@kanto.dev", "password": "Eevee1234"}
    )
    token = login(client, "gary", "Eevee1234").json()["access_token"]
    response = client.post(
        POKEDEX,
        json={"pokemon_id": 58, "nickname": "Rufo", "is_captured": True},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 201
    return response.json()


@pytest.fixture
def pokeapi():
    """PokeAPI simulada para probar el servicio sin red."""
    with respx.mock(base_url=BASE, assert_all_called=False) as router:
        yield router


def make_service() -> PokeAPIService:
    settings = get_settings().model_copy(
        update={"pokeapi_base_url": BASE, "pokeapi_backoff_seconds": 0}
    )
    return PokeAPIService(httpx.AsyncClient(base_url=BASE), settings)


# ═════════════════════════════════════════════════════════════
#  Autenticación
# ═════════════════════════════════════════════════════════════
def test_register_user_success(client: TestClient) -> None:
    """Registro exitoso de usuario."""
    response = client.post(
        REGISTER, json={"username": "Misty", "email": "Misty@Cerulean.dev", "password": "Starmie42"}
    )

    assert response.status_code == 201
    body = response.json()
    assert body["username"] == "misty"
    assert body["email"] == "misty@cerulean.dev"
    assert body["is_active"] is True
    assert "password" not in body and "hashed_password" not in body


def test_register_duplicate_username(client: TestClient, test_user: dict) -> None:
    """Error al registrar un username duplicado (sin distinguir mayúsculas)."""
    response = client.post(
        REGISTER, json={"username": "ASH", "email": "otro@kanto.dev", "password": "Pikachu123"}
    )

    assert response.status_code == 409


def test_login_success(client: TestClient, test_user: dict) -> None:
    """Login exitoso: JWT válido con sub, user_id, exp e iat, y 24 horas de validez."""
    response = login(client, "ash", test_user["password"])

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["expires_in"] == 24 * 60 * 60

    settings = get_settings()
    claims = jwt.decode(
        body["access_token"],
        settings.secret_key.get_secret_value(),
        algorithms=["HS256"],
        audience=settings.jwt_audience,
    )
    assert claims["sub"] == "ash"
    assert claims["user_id"] == test_user["id"]
    assert claims["exp"] - claims["iat"] == 24 * 60 * 60


def test_login_invalid_credentials(client: TestClient, test_user: dict) -> None:
    """Login con credenciales incorrectas: 401."""
    response = login(client, "ash", "Incorrecta123")

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_access_protected_endpoint_without_token(client: TestClient) -> None:
    """Acceso sin token: 401."""
    response = client.get(ME)

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_passwords_are_stored_hashed(client: TestClient, session: Session) -> None:
    """La contraseña nunca se guarda en claro: solo su hash bcrypt."""
    client.post(
        REGISTER, json={"username": "brock", "email": "brock@kanto.dev", "password": "Onix12345"}
    )

    user = session.exec(select(User).where(User.username == "brock")).one()
    assert user.hashed_password != "Onix12345"
    assert user.hashed_password.startswith("$2b$")  # bcrypt


# ═════════════════════════════════════════════════════════════
#  Pokédex
# ═════════════════════════════════════════════════════════════
def test_add_pokemon_to_pokedex(add) -> None:
    """Añadir un Pokémon guarda los datos copiados de PokeAPI."""
    entry = add(4, nickname="Llamita", is_captured=True)

    assert entry["pokemon_id"] == 4
    assert entry["pokemon_name"] == "charmander"
    assert entry["pokemon_types"] == ["fire"]
    assert entry["pokemon_sprite"].endswith("/4.png")
    assert entry["nickname"] == "Llamita"
    assert entry["is_captured"] is True
    assert entry["capture_date"] is not None  # capturado sin fecha: se usa la actual


def test_add_duplicate_pokemon(add) -> None:
    """No se permite el mismo Pokémon dos veces en la misma Pokédex."""
    add(4)

    assert add(4, expected=409)["detail"] == "Ese Pokémon ya está en tu Pokédex."


def test_get_pokedex_with_filters(client: TestClient, auth_headers: dict, add) -> None:
    """Los filtros captured y favorite funcionan por separado y combinados."""
    add(4, is_captured=True, favorite=True)
    add(37, is_captured=True)
    add(58, favorite=True)

    def listed(**params) -> list[str]:
        response = client.get(POKEDEX, params=params, headers=auth_headers)
        return [e["pokemon_name"] for e in response.json()["results"]]

    assert listed(captured="true") == ["charmander", "vulpix"]
    assert listed(captured="false") == ["growlithe"]
    assert listed(favorite="true") == ["charmander", "growlithe"]
    assert listed(captured="true", favorite="false") == ["vulpix"]


def test_update_pokedex_entry(client: TestClient, auth_headers: dict, add) -> None:
    """PATCH modifica solo los campos enviados."""
    entry = add(4, notes="Notas originales")

    response = client.patch(
        f"{POKEDEX}/{entry['id']}",
        json={
            "is_captured": True,
            "capture_date": "2025-01-13T10:00:00Z",
            "nickname": "Rayo",
            "favorite": True,
        },
        headers=auth_headers,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["is_captured"] is True
    assert body["capture_date"].startswith("2025-01-13T10:00:00")
    assert body["nickname"] == "Rayo"
    assert body["favorite"] is True
    assert body["notes"] == "Notas originales"


def test_delete_pokedex_entry(client: TestClient, auth_headers: dict, add) -> None:
    """DELETE elimina la entrada; después ya no existe."""
    entry = add(4)

    assert client.delete(f"{POKEDEX}/{entry['id']}", headers=auth_headers).status_code == 204
    assert client.get(f"{POKEDEX}/{entry['id']}", headers=auth_headers).status_code == 404
    assert client.delete(f"{POKEDEX}/{entry['id']}", headers=auth_headers).status_code == 404


def test_cannot_modify_other_user_pokedex(
    client: TestClient, auth_headers: dict, other_user_entry: dict
) -> None:
    """La entrada de otro usuario es invisible: 404 al leerla, modificarla o borrarla."""
    url = f"{POKEDEX}/{other_user_entry['id']}"

    assert client.get(url, headers=auth_headers).status_code == 404
    assert client.patch(url, json={"nickname": "Robado"}, headers=auth_headers).status_code == 404
    assert client.delete(url, headers=auth_headers).status_code == 404
    assert client.get(POKEDEX, headers=auth_headers).json()["count"] == 0


# ═════════════════════════════════════════════════════════════
#  Integración con PokeAPI
# ═════════════════════════════════════════════════════════════
async def test_pokeapi_service_get_pokemon(pokeapi) -> None:
    """El servicio obtiene un Pokémon y extrae solo los campos relevantes."""
    pokeapi.get("/pokemon/25").respond(json=pokemon_payload(types=("electric", "fairy")))

    pokemon = await make_service().get_pokemon(25)

    assert pokemon.id == 25
    assert pokemon.name == "pikachu"
    assert pokemon.types == ["electric", "fairy"]
    assert [a.name for a in pokemon.abilities] == ["static", "lightning-rod"]
    assert pokemon.stats.hp == 10
    assert pokemon.stats.speed == 60
    assert not hasattr(pokemon, "moves")  # solo los campos útiles


async def test_pokeapi_service_handles_404(pokeapi) -> None:
    """Un Pokémon inexistente produce PokemonNotFoundError y no se reintenta."""
    route = pokeapi.get("/pokemon/missingno").respond(404)

    with pytest.raises(PokemonNotFoundError):
        await make_service().get_pokemon("missingno")

    assert route.call_count == 1


# ═════════════════════════════════════════════════════════════
#  Rate limiting
# ═════════════════════════════════════════════════════════════
def test_rate_limit_exceeded(client: TestClient) -> None:
    """La 11.ª petición de login en un minuto devuelve 429.

    Cada intento usa un usuario distinto para que el 429 sea del rate limit por IP y no
    del bloqueo por intentos fallidos (que cuenta por usuario e IP).
    """
    for i in range(10):
        assert login(client, f"nadie{i}", "Mal12345").status_code == 401

    response = login(client, "nadie10", "Mal12345")

    assert response.status_code == 429
    assert response.json()["limit"] == "10 per 1 minute"
    assert int(response.headers["retry-after"]) > 0
    assert response.headers["x-ratelimit-remaining"] == "0"
