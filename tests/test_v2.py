"""Tests de la API v2 (evoluciones y cursor) y de la deprecación de la v1."""

import base64
import json
import logging
from collections.abc import Iterator
from datetime import UTC, datetime

import httpx
import pytest
import respx
from fastapi.testclient import TestClient
from sqlmodel import Session

from app.config import get_settings
from app.models import PokedexEntry, User
from app.routers.v2 import decode_cursor, encode_cursor
from app.services.pokeapi_service import (
    InvalidIdentifierError,
    PokeAPIService,
    evolution_condition,
)
from tests.fakes import POKEAPI

V2 = "/api/v2/pokedex"
V1 = "/api/v1/pokedex"


def node(pid: int, name: str, details: list | None = None, evolves_to: list | None = None) -> dict:
    return {
        "species": {"name": name, "url": f"{POKEAPI}/pokemon-species/{pid}/"},
        "evolution_details": details or [],
        "evolves_to": evolves_to or [],
    }


def level(n: int) -> dict:
    return {"trigger": {"name": "level-up"}, "min_level": n}


def stone(item: str) -> dict:
    return {"trigger": {"name": "use-item"}, "item": {"name": item}}


CHAINS = {
    1: node(
        1, "bulbasaur",
        evolves_to=[node(2, "ivysaur", [level(16)], [node(3, "venusaur", [level(32)])])],
    ),
    10: node(
        172,
        "pichu",
        evolves_to=[
            node(
                25,
                "pikachu",
                [{"trigger": {"name": "level-up"}, "min_happiness": 220}],
                [node(26, "raichu", [stone("thunder-stone")])],
            )
        ],
    ),
    67: node(
        133,
        "eevee",
        evolves_to=[
            node(134, "vaporeon", [stone("water-stone")]),
            node(196, "espeon", [{"trigger": {"name": "level-up"},
                                  "min_happiness": 160, "time_of_day": "day"}]),
        ],
    ),
    72: node(150, "mewtwo"),
}  # fmt: skip
SPECIES_CHAIN = {1: 1, 2: 1, 3: 1, 25: 10, 26: 10, 172: 10, 133: 67, 134: 67, 150: 72}
POKEMON = {1: "bulbasaur", 25: "pikachu", 133: "eevee", 150: "mewtwo", 3: "venusaur", 9999: "x"}


def fake_species(request: httpx.Request, pid: str) -> httpx.Response:
    pid_int = int(pid) if pid.isdigit() else next(k for k, v in POKEMON.items() if v == pid)
    chain = SPECIES_CHAIN.get(pid_int)
    return httpx.Response(
        200,
        json={
            "id": pid_int,
            "name": POKEMON.get(pid_int, "x"),
            "names": [],
            "genera": [],
            "flavor_text_entries": [],
            "evolution_chain": {"url": f"{POKEAPI}/evolution-chain/{chain}/"} if chain else None,
        },
    )


def fake_chain(request: httpx.Request, cid: str) -> httpx.Response:
    return httpx.Response(200, json={"id": int(cid), "chain": CHAINS[int(cid)]})


@pytest.fixture
def api() -> Iterator[respx.MockRouter]:
    with respx.mock(assert_all_called=False) as router:
        router.get(url__regex=rf"^{POKEAPI}/pokemon-species/(?P<pid>[a-z0-9-]+)$").mock(
            side_effect=fake_species
        )
        router.get(url__regex=rf"^{POKEAPI}/evolution-chain/(?P<cid>\d+)$").mock(
            side_effect=fake_chain
        )
        yield router


@pytest.fixture
def pokedex(session: Session, test_user: dict) -> list[int]:
    ids = []
    for pid, captured in ((25, True), (133, False), (1, True), (150, True), (3, False)):
        entry = PokedexEntry(
            owner_id=test_user["id"],
            pokemon_id=pid,
            pokemon_name=POKEMON[pid],
            pokemon_types=["normal"],
            is_captured=captured,
        )
        session.add(entry)
        session.commit()
        ids.append(entry.id)
    return ids


def by_name(body: dict) -> dict[str, dict]:
    return {e["pokemon_name"]: e for e in body["items"]}


# ═════════════════════════════════════════════════════════════
#  v2: evoluciones
# ═════════════════════════════════════════════════════════════
def test_v2_includes_evolution_data(client: TestClient, auth_headers: dict, api, pokedex) -> None:
    body = client.get(V2, params={"limit": 10}, headers=auth_headers).json()
    items = by_name(body)

    pikachu = items["pikachu"]["evolution"]
    assert (pikachu["stage"], pikachu["total_stages"]) == (2, 3)
    assert pikachu["evolves_from"] == "pichu"
    assert pikachu["evolves_to"] == [
        {"id": 26, "name": "raichu", "condition": "usar Piedra Trueno"}
    ]
    assert pikachu["is_final_stage"] is False

    eevee = items["eevee"]["evolution"]
    assert [t["name"] for t in eevee["evolves_to"]] == ["vaporeon", "espeon"]
    assert eevee["evolves_to"][1]["condition"] == "amistad alta, de día"

    assert items["venusaur"]["evolution"]["is_final_stage"] is True
    assert items["mewtwo"]["evolution"]["total_stages"] == 1
    # La entrada conserva todos los campos de la v1
    assert items["pikachu"]["pokemon_id"] == 25 and "created_at" in items["pikachu"]


def test_entries_without_a_matching_chain_have_no_evolution(
    client: TestClient,
    auth_headers: dict,
    api,
    session: Session,
    test_user: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delitem(SPECIES_CHAIN, 150)  # especie sin cadena evolutiva
    for pid, name in ((150, "mewtwo"), (25, "pikachu-rock-star")):  # forma con otro nombre
        session.add(PokedexEntry(owner_id=test_user["id"], pokemon_id=pid, pokemon_name=name))
    session.commit()

    items = by_name(client.get(V2, headers=auth_headers).json())

    assert items["mewtwo"]["evolution"] is None
    assert items["pikachu-rock-star"]["evolution"] is None


def test_v2_without_pokeapi_still_lists_the_pokedex(
    client: TestClient, auth_headers: dict, pokedex
) -> None:
    async def no_sleep(_: float) -> None:
        return None

    client.app.state.pokeapi._sleep = no_sleep
    with respx.mock() as router:
        router.route(host="pokeapi.co").mock(side_effect=httpx.ConnectTimeout("caída"))
        response = client.get(V2, params={"limit": 10}, headers=auth_headers)

    assert response.status_code == 200
    items = by_name(response.json())
    assert len(items) == 5
    # Los 151 originales tienen especie de respaldo, pero no la cadena evolutiva
    assert all(e["evolution"] is None for e in items.values())


def test_v2_only_lists_my_pokedex(
    client: TestClient, auth_headers: dict, api, pokedex, session: Session
) -> None:
    gary = User(username="gary", email="gary@kanto.dev", hashed_password="x")
    session.add(gary)
    session.commit()
    session.add(PokedexEntry(owner_id=gary.id, pokemon_id=7, pokemon_name="squirtle"))
    session.commit()

    names = by_name(client.get(V2, params={"limit": 100}, headers=auth_headers).json())

    assert "squirtle" not in names


# ═════════════════════════════════════════════════════════════
#  v2: paginación por cursor
# ═════════════════════════════════════════════════════════════
def test_cursor_pagination_walks_every_entry_once(
    client: TestClient, auth_headers: dict, api, pokedex
) -> None:
    seen, cursor, pages = [], None, 0
    while True:
        params = {"limit": 2} | ({"cursor": cursor} if cursor else {})
        body = client.get(V2, params=params, headers=auth_headers).json()
        seen += [e["id"] for e in body["items"]]
        cursor, pages = body["next_cursor"], pages + 1
        if cursor is None:
            break

    assert seen == pokedex
    assert pages == 3


def test_cursor_is_stable_when_entries_are_added(
    client: TestClient, auth_headers: dict, api, pokedex, session: Session, test_user: dict
) -> None:
    """Con offset, insertar mientras se pagina repetiría elementos; con cursor, no."""
    first = client.get(V2, params={"limit": 2}, headers=auth_headers).json()
    session.add(PokedexEntry(owner_id=test_user["id"], pokemon_id=26, pokemon_name="raichu"))
    session.commit()

    second = client.get(
        V2, params={"limit": 2, "cursor": first["next_cursor"]}, headers=auth_headers
    ).json()

    first_ids = {e["id"] for e in first["items"]}
    assert first_ids.isdisjoint(e["id"] for e in second["items"])


def test_cursor_works_with_filters(client: TestClient, auth_headers: dict, api, pokedex) -> None:
    first = client.get(V2, params={"limit": 1, "captured": "true"}, headers=auth_headers).json()
    second = client.get(
        V2,
        params={"limit": 10, "captured": "true", "cursor": first["next_cursor"]},
        headers=auth_headers,
    ).json()

    names = [e["pokemon_name"] for e in first["items"] + second["items"]]
    assert names == ["pikachu", "bulbasaur", "mewtwo"]
    assert second["next_cursor"] is None


def b64(data: object) -> str:
    return base64.urlsafe_b64encode(json.dumps(data).encode()).decode().rstrip("=")


@pytest.mark.parametrize(
    "cursor",
    [
        "no-vale",
        "%%%",
        b64({"v": 2, "after": 1}),
        b64({"v": 1, "after": -1}),
        b64({"v": 1, "after": "x"}),
        b64({"v": 1}),
        b64([1, 2]),
    ],
)
def test_invalid_cursors_are_rejected(client: TestClient, auth_headers: dict, cursor: str) -> None:
    response = client.get(V2, params={"cursor": cursor}, headers=auth_headers)

    assert response.status_code == 422


def test_cursor_round_trip() -> None:
    assert decode_cursor(encode_cursor(42)) == 42
    assert "=" not in encode_cursor(7)


def test_v2_requires_authentication(client: TestClient) -> None:
    assert client.get(V2).status_code == 401
    assert client.get("/api/v2/pokemon/eevee/evolution").status_code == 401


# ═════════════════════════════════════════════════════════════
#  v2: cadena evolutiva
# ═════════════════════════════════════════════════════════════
@pytest.fixture
def pokemon_routes(api: respx.MockRouter) -> respx.MockRouter:
    def fake_pokemon(request: httpx.Request, name: str) -> httpx.Response:
        if name not in POKEMON.values():
            return httpx.Response(404)
        pid = next(k for k, v in POKEMON.items() if v == name)
        return httpx.Response(
            200,
            json={
                "id": pid,
                "name": name,
                "species": {"name": name, "url": "x"},
                "types": [],
                "abilities": [],
                "stats": [],
                "sprites": {},
            },
        )

    api.get(url__regex=rf"^{POKEAPI}/pokemon/(?P<name>[a-z]+)$").mock(side_effect=fake_pokemon)
    return api


def test_evolution_chain_endpoint(client: TestClient, auth_headers: dict, pokemon_routes) -> None:
    response = client.get("/api/v2/pokemon/eevee/evolution", headers=auth_headers)

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == 67
    assert body["stages"] == 2
    assert [(s["name"], s["stage"], s["evolves_from"]) for s in body["steps"]] == [
        ("eevee", 1, None),
        ("vaporeon", 2, "eevee"),
        ("espeon", 2, "eevee"),
    ]


def test_evolution_of_unknown_pokemon(
    client: TestClient, auth_headers: dict, pokemon_routes
) -> None:
    assert (
        client.get("/api/v2/pokemon/missingno/evolution", headers=auth_headers).status_code == 404
    )


def test_species_without_chain(
    client: TestClient, auth_headers: dict, pokemon_routes, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delitem(SPECIES_CHAIN, 150)

    response = client.get("/api/v2/pokemon/mewtwo/evolution", headers=auth_headers)

    assert response.status_code == 404
    assert response.json()["detail"] == "Este Pokémon no tiene cadena evolutiva."


@pytest.mark.parametrize(
    ("details", "expected"),
    [
        ([], None),
        ([level(16)], "nivel 16"),
        ([{"trigger": {"name": "level-up"}}], "subir de nivel"),
        ([stone("moon-stone")], "usar Piedra Lunar"),
        ([stone("kings-rock")], "usar Kings Rock"),
        ([{"trigger": {"name": "trade"}}], "intercambio"),
        (
            [{"trigger": {"name": "trade"}, "held_item": {"name": "metal-coat"}}],
            "intercambio, llevando Metal Coat",
        ),
        (
            [{"trigger": {"name": "level-up"}, "known_move_type": {"name": "fairy"}}],
            "conociendo un movimiento de tipo Hada",
        ),
        ([{"trigger": {"name": "level-up"}, "min_happiness": 160, "time_of_day": "night"}],
         "amistad alta, de noche"),
        ([{"trigger": {"name": "shed"}}], "Shed"),
        ([{"trigger": None}], None),
    ],
)  # fmt: skip
def test_evolution_conditions_in_spanish(details: list, expected: str | None) -> None:
    assert evolution_condition(details) == expected


async def test_invalid_chain_id() -> None:
    service = PokeAPIService()
    with pytest.raises(InvalidIdentifierError):
        await service.get_evolution_chain(0)
    await service.close()


# ═════════════════════════════════════════════════════════════
#  Deprecación de la v1
# ═════════════════════════════════════════════════════════════
def test_v1_list_announces_its_deprecation(client: TestClient, auth_headers: dict) -> None:
    response = client.get(V1, headers=auth_headers)

    assert response.status_code == 200  # sigue funcionando igual
    assert response.headers["deprecation"] == "@1794182400"  # 9 nov 2026
    assert response.headers["sunset"] == "Wed, 30 Jun 2027 23:59:59 GMT"
    assert response.headers["link"] == '</api/v2/pokedex>; rel="successor-version"'


def test_other_v1_endpoints_are_not_deprecated(client: TestClient, auth_headers: dict) -> None:
    assert "deprecation" not in client.get("/api/v1/pokedex/stats", headers=auth_headers).headers


def test_openapi_marks_v1_list_as_deprecated(client: TestClient) -> None:
    paths = client.get("/openapi.json").json()["paths"]

    assert paths["/api/v1/pokedex"]["get"]["deprecated"] is True
    assert "deprecated" not in paths["/api/v2/pokedex"]["get"]


def test_v1_usage_is_logged(
    client: TestClient, auth_headers: dict, test_user: dict, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO, logger="pokedex_api"):
        client.get(V1, headers=auth_headers)

    assert (
        f"Uso de endpoint obsoleto GET /api/v1/pokedex (user_id={test_user['id']})" in caplog.text
    )


def test_v1_is_gone_after_sunset(
    client: TestClient, auth_headers: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "v1_pokedex_sunset", datetime(2026, 1, 1, tzinfo=UTC))

    response = client.get(V1, headers=auth_headers)

    assert response.status_code == 410
    assert response.headers["link"] == '</api/v2/pokedex>; rel="successor-version"'
    assert "/api/v2/pokedex" in response.json()["detail"]
