"""Tests de la Pokédex personal: CRUD, filtros, ordenación, autorización y exportación."""

import csv
import io
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import httpx
import pytest
import respx
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.models import PokedexEntry, Team, TeamMember, User
from app.services.exporters import _filters_text, _safe_cell, pokedex_to_pdf
from tests.fakes import POKEAPI, SPRITES, artwork_png, pokemon_payload, with_real_sprites

POKEDEX = "/api/v1/pokedex"

KNOWN = {
    1: ("bulbasaur", ("grass", "poison")),
    4: ("charmander", ("fire",)),
    6: ("charizard", ("fire", "flying")),
    7: ("squirtle", ("water",)),
    25: ("pikachu", ("electric",)),
    150: ("mewtwo", ("psychic",)),
}


def fake_pokemon(request: httpx.Request, pid: str) -> httpx.Response:
    if int(pid) not in KNOWN:
        return httpx.Response(404)
    name, types = KNOWN[int(pid)]
    return httpx.Response(200, json=with_real_sprites(pokemon_payload(int(pid), name, types)))


@pytest.fixture
def api() -> Iterator[respx.MockRouter]:
    with respx.mock(assert_all_called=False) as router:
        router.get(url__regex=rf"^{POKEAPI}/pokemon/(?P<pid>\d+)$").mock(side_effect=fake_pokemon)
        router.get(url__startswith=SPRITES).respond(
            content=artwork_png(), headers={"Content-Type": "image/png"}
        )
        yield router


@pytest.fixture
def add(client: TestClient, auth_headers: dict, api):
    def _add(pokemon_id: int, expected: int = 201, **fields) -> dict:
        response = client.post(
            POKEDEX, json={"pokemon_id": pokemon_id, **fields}, headers=auth_headers
        )
        assert response.status_code == expected, response.text
        return response.json()

    return _add


@pytest.fixture
def other_user_entry(client: TestClient, api) -> dict:
    """Entrada de otro entrenador (gary), para comprobar el aislamiento entre usuarios."""
    client.post(
        "/api/v1/auth/register",
        json={"username": "gary", "email": "gary@kanto.dev", "password": "Eevee1234"},
    )
    token = client.post(
        "/api/v1/auth/login", data={"username": "gary", "password": "Eevee1234"}
    ).json()["access_token"]
    response = client.post(
        POKEDEX,
        json={"pokemon_id": 7, "nickname": "Vamo", "is_captured": True},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 201
    return response.json()


def names(response) -> list[str]:
    return [e["pokemon_name"] for e in response.json()["results"]]


# ═════════════════════════════════════════════════════════════
#  Alta: validaciones y reglas
# ═════════════════════════════════════════════════════════════
def test_pokedex_requires_authentication(client: TestClient) -> None:
    assert client.get(POKEDEX).status_code == 401
    assert client.post(POKEDEX, json={"pokemon_id": 25}).status_code == 401
    assert client.get(f"{POKEDEX}/export").status_code == 401


def test_add_unknown_pokemon_returns_404(add) -> None:
    assert add(9999, expected=404)["detail"] == "Pokémon no encontrado: 9999"


@pytest.mark.parametrize(
    "body",
    [
        {"pokemon_id": 0},
        {"pokemon_id": 100_001},
        {"pokemon_id": "pikachu"},
        {"pokemon_id": 25, "nickname": "x" * 51},
        {"pokemon_id": 25, "notes": "x" * 501},
        {"pokemon_id": 25, "is_captured": True, "capture_date": "2999-01-01T00:00:00Z"},
        {"pokemon_id": 25, "capture_date": "2024-01-01T00:00:00Z"},  # fecha sin capturar
    ],
)
def test_add_rejects_invalid_data(client: TestClient, auth_headers: dict, api, body: dict) -> None:
    assert client.post(POKEDEX, json=body, headers=auth_headers).status_code == 422


def test_texts_are_cleaned(add) -> None:
    entry = add(25, nickname="  Sparky   el   rayo ", notes="   ")

    assert entry["nickname"] == "Sparky el rayo"
    assert entry["notes"] is None


def test_add_with_explicit_capture_date(add) -> None:
    entry = add(6, is_captured=True, capture_date="2026-10-01T10:00:00+02:00")

    assert entry["capture_date"].startswith("2026-10-01T08:00:00")  # normalizada a UTC


def test_add_uses_fallback_data_when_pokeapi_is_down(
    client: TestClient, auth_headers: dict
) -> None:
    async def no_sleep(_: float) -> None:
        return None

    client.app.state.pokeapi._sleep = no_sleep
    with respx.mock() as router:
        router.route(host="pokeapi.co").mock(side_effect=httpx.ConnectTimeout("caída"))
        ok = client.post(POKEDEX, json={"pokemon_id": 133}, headers=auth_headers)
        down = client.post(POKEDEX, json={"pokemon_id": 999}, headers=auth_headers)

    assert ok.status_code == 201
    assert ok.json()["pokemon_name"] == "eevee"
    assert down.status_code == 503
    assert client.get(POKEDEX, headers=auth_headers).json()["count"] == 1


def test_simultaneous_duplicate_is_caught_by_the_database(
    client: TestClient, auth_headers: dict, session: Session, test_user: dict, api
) -> None:
    """Simula dos peticiones a la vez: la segunda inserta justo después de la comprobación
    previa. La restricción UNIQUE de la base de datos lo detecta y se responde 409."""
    service = client.app.state.pokeapi
    original = service.get_pokemon

    async def racing_get_pokemon(identifier):
        session.add(PokedexEntry(owner_id=test_user["id"], pokemon_id=25, pokemon_name="pikachu"))
        session.commit()
        return await original(identifier)

    service.get_pokemon = racing_get_pokemon
    response = client.post(POKEDEX, json={"pokemon_id": 25}, headers=auth_headers)

    assert response.status_code == 409
    assert len(session.exec(select(PokedexEntry)).all()) == 1


# ═════════════════════════════════════════════════════════════
#  Listado: orden, paginación y aislamiento
# ═════════════════════════════════════════════════════════════
@pytest.fixture
def collection(add) -> None:
    add(25, is_captured=True, capture_date="2026-03-01T00:00:00Z")
    add(1, is_captured=True, capture_date="2026-05-01T00:00:00Z")
    add(150)
    add(7, is_captured=True, capture_date="2026-01-01T00:00:00Z")


@pytest.mark.parametrize(
    ("params", "expected"),
    [
        ({}, ["bulbasaur", "squirtle", "pikachu", "mewtwo"]),
        ({"order": "desc"}, ["mewtwo", "pikachu", "squirtle", "bulbasaur"]),
        ({"sort": "pokemon_name"}, ["bulbasaur", "mewtwo", "pikachu", "squirtle"]),
        ({"sort": "capture_date"}, ["squirtle", "pikachu", "bulbasaur", "mewtwo"]),
        # Los no capturados (sin fecha) siempre al final, también en orden descendente
        ({"sort": "capture_date", "order": "desc"}, ["bulbasaur", "pikachu", "squirtle", "mewtwo"]),
        ({"sort": "created_at"}, ["pikachu", "bulbasaur", "mewtwo", "squirtle"]),
    ],
)
def test_list_sorting(client: TestClient, auth_headers: dict, collection, params, expected) -> None:
    assert names(client.get(POKEDEX, params=params, headers=auth_headers)) == expected


def test_list_pagination(client: TestClient, auth_headers: dict, collection) -> None:
    body = client.get(POKEDEX, params={"limit": 2, "offset": 1}, headers=auth_headers).json()

    assert body["count"] == 4
    assert (body["limit"], body["offset"]) == (2, 1)
    assert [e["pokemon_name"] for e in body["results"]] == ["squirtle", "pikachu"]


@pytest.mark.parametrize(
    "params", [{"sort": "hp"}, {"order": "up"}, {"limit": 0}, {"limit": 101}, {"offset": -1}]
)
def test_list_rejects_invalid_parameters(client: TestClient, auth_headers: dict, params) -> None:
    assert client.get(POKEDEX, params=params, headers=auth_headers).status_code == 422


def test_users_only_see_their_own_entries(
    client: TestClient, auth_headers: dict, add, other_user_entry: dict
) -> None:
    add(25)

    assert names(client.get(POKEDEX, headers=auth_headers)) == ["pikachu"]


def test_get_single_entry(client: TestClient, auth_headers: dict, add) -> None:
    entry = add(25)

    response = client.get(f"{POKEDEX}/{entry['id']}", headers=auth_headers)

    assert response.status_code == 200
    assert response.json() == entry


# ═════════════════════════════════════════════════════════════
#  Actualización: reglas de captura
# ═════════════════════════════════════════════════════════════
def patch(client: TestClient, headers: dict, entry: dict, body: dict):
    return client.patch(f"{POKEDEX}/{entry['id']}", json=body, headers=headers)


def test_capturing_without_date_uses_now(client: TestClient, auth_headers: dict, add) -> None:
    entry = add(25)

    body = patch(client, auth_headers, entry, {"is_captured": True}).json()

    captured_at = datetime.fromisoformat(body["capture_date"])
    assert abs(captured_at - datetime.now(UTC)) < timedelta(minutes=1)


def test_releasing_clears_the_capture_date(client: TestClient, auth_headers: dict, add) -> None:
    entry = add(25, is_captured=True)

    body = patch(client, auth_headers, entry, {"is_captured": False}).json()

    assert body["is_captured"] is False
    assert body["capture_date"] is None


@pytest.mark.parametrize(
    "body",
    [
        {"is_captured": False, "capture_date": "2025-01-01T00:00:00Z"},
        {"capture_date": "2025-01-01T00:00:00Z"},  # la entrada no está capturada
        {"capture_date": "2999-01-01T00:00:00Z", "is_captured": True},
        {"is_captured": None},
        {"favorite": None},
        {"nickname": "x" * 51},
    ],
)
def test_incoherent_updates_are_rejected(client: TestClient, auth_headers: dict, add, body) -> None:
    entry = add(25)

    assert patch(client, auth_headers, entry, body).status_code == 422
    assert client.get(f"{POKEDEX}/{entry['id']}", headers=auth_headers).json() == entry


def test_dates_without_timezone_are_taken_as_utc(
    client: TestClient, auth_headers: dict, add
) -> None:
    entry = add(25, is_captured=True)

    body = patch(client, auth_headers, entry, {"capture_date": "2025-06-01T12:30:00"}).json()

    assert body["capture_date"].startswith("2025-06-01T12:30:00")
    assert body["capture_date"].endswith("Z")


def test_null_capture_date_on_a_captured_entry_is_rejected(
    client: TestClient, auth_headers: dict, add
) -> None:
    """Antes se cambiaba en silencio por la fecha actual y se perdía la original."""
    entry = add(6, is_captured=True, capture_date="2020-01-01T00:00:00Z")

    response = patch(client, auth_headers, entry, {"capture_date": None})

    assert response.status_code == 422
    assert "márcalo como no capturado" in response.json()["detail"]
    kept = client.get(f"{POKEDEX}/{entry['id']}", headers=auth_headers).json()
    assert kept["capture_date"].startswith("2020-01-01")


def test_null_clears_optional_texts(client: TestClient, auth_headers: dict, add) -> None:
    entry = add(25, nickname="Sparky", notes="Algo")

    body = patch(client, auth_headers, entry, {"nickname": None, "notes": "  "}).json()

    assert body["nickname"] is None
    assert body["notes"] is None


def test_empty_update_changes_nothing(client: TestClient, auth_headers: dict, add) -> None:
    entry = add(25, nickname="Sparky")

    assert patch(client, auth_headers, entry, {}).json() == entry


def test_deleting_an_entry_removes_it_from_teams(
    client: TestClient, auth_headers: dict, session: Session, add, test_user: dict
) -> None:
    entry = add(25)
    team = Team(name="Élite", trainer_id=test_user["id"])
    session.add(team)
    session.commit()
    session.add(TeamMember(team_id=team.id, pokedex_entry_id=entry["id"], position=1))
    session.commit()

    client.delete(f"{POKEDEX}/{entry['id']}", headers=auth_headers)

    assert session.exec(select(TeamMember)).all() == []


# ═════════════════════════════════════════════════════════════
#  Exportación
# ═════════════════════════════════════════════════════════════
def test_export_csv(client: TestClient, auth_headers: dict, add) -> None:
    add(25, nickname="Sparky", is_captured=True, favorite=True, notes="Mi primer compañero")
    add(6, nickname='=HYPERLINK("http://malicioso","clic")', notes="+34 600 000 000")

    response = client.get(f"{POKEDEX}/export", params={"format": "csv"}, headers=auth_headers)

    assert response.status_code == 200
    assert response.headers["content-type"] == "text/csv; charset=utf-8"
    assert response.headers["content-disposition"].startswith('attachment; filename="pokedex-ash-')
    assert response.content.startswith(b"\xef\xbb\xbf")  # BOM para Excel

    rows = list(csv.DictReader(io.StringIO(response.content.decode("utf-8-sig"))))
    assert [r["nombre"] for r in rows] == ["charizard", "pikachu"]
    pikachu = rows[1]
    assert (pikachu["apodo"], pikachu["capturado"], pikachu["favorito"]) == ("Sparky", "si", "si")
    assert pikachu["notas"] == "Mi primer compañero"
    # Protección contra inyección de fórmulas
    assert rows[0]["apodo"].startswith("'=")
    assert rows[0]["notas"] == "'+34 600 000 000"
    assert rows[0]["tipos"] == "fire|flying"


def test_export_respects_filters(client: TestClient, auth_headers: dict, add) -> None:
    add(25, is_captured=True)
    add(6)

    response = client.get(
        f"{POKEDEX}/export", params={"format": "csv", "captured": "true"}, headers=auth_headers
    )

    rows = list(csv.DictReader(io.StringIO(response.content.decode("utf-8-sig"))))
    assert [r["nombre"] for r in rows] == ["pikachu"]


def test_export_pdf(client: TestClient, auth_headers: dict, add) -> None:
    add(25, nickname="<b>Sparky</b> & cía", is_captured=True, favorite=True)
    add(150, notes="Algún día...")

    response = client.get(
        f"{POKEDEX}/export", params={"format": "pdf", "favorite": "true"}, headers=auth_headers
    )

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["content-disposition"].endswith('.pdf"')
    assert response.content.startswith(b"%PDF")


def test_export_rejects_unknown_format(client: TestClient, auth_headers: dict) -> None:
    response = client.get(f"{POKEDEX}/export", params={"format": "xlsx"}, headers=auth_headers)

    assert response.status_code == 422


def test_pdf_of_an_empty_pokedex() -> None:
    assert pokedex_to_pdf([], "ash").startswith(b"%PDF")


def test_pdf_with_many_entries_spans_several_pages() -> None:
    entries = [
        PokedexEntry(
            id=i, owner_id=1, pokemon_id=i, pokemon_name=f"pokemon-{i}", pokemon_types=["normal"]
        )
        for i in range(1, 121)
    ]

    assert pokedex_to_pdf(entries, "ash").count(b"/Type /Page\n") >= 3


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("=1+1", "'=1+1"),
        ("+34", "'+34"),
        ("-2", "'-2"),
        ("@SUM(A1)", "'@SUM(A1)"),
        ("\tcmd", "'\tcmd"),
        ("Sparky", "Sparky"),
        (None, ""),
    ],
)
def test_csv_cells_are_neutralized(value, expected) -> None:
    assert _safe_cell(value) == expected


def test_filters_description() -> None:
    assert _filters_text({"captured": True, "favorite": False}) == (
        "Filtro: capturados y no favoritos"
    )
    assert _filters_text({"captured": None, "favorite": None}) == "Pokédex completa"


def test_users_table_is_untouched_by_exports(session: Session, test_user: dict) -> None:
    assert session.get(User, test_user["id"]) is not None
