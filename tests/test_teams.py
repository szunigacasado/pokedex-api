"""Tests de los equipos de batalla: CRUD, reglas, aislamiento, análisis y PDF."""

from collections.abc import Iterator

import httpx
import pytest
import respx
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.models import (
    PokedexEntry,
    PokemonAbility,
    PokemonDetail,
    PokemonSprites,
    PokemonStats,
    Team,
    TeamMember,
)
from app.routers.teams import MAX_TEAMS_PER_USER, _ascii_slug
from app.services.team_analysis import (
    analyze_team,
    defensive_matchups,
    effectiveness,
    es_number,
    offensive_coverage,
)
from tests.fakes import POKEAPI, SPRITES, artwork_png, pokemon_payload, with_real_sprites

TEAMS = "/api/v1/teams"

KNOWN = {
    1: ("bulbasaur", ("grass", "poison")),
    4: ("charmander", ("fire",)),
    6: ("charizard", ("fire", "flying")),
    7: ("squirtle", ("water",)),
    25: ("pikachu", ("electric",)),
    37: ("vulpix", ("fire",)),
    58: ("growlithe", ("fire",)),
    94: ("gengar", ("ghost", "poison")),
    133: ("eevee", ("normal",)),
    143: ("snorlax", ("normal",)),
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
        router.get(url__startswith=f"{POKEAPI}/pokemon-species/").respond(404)
        router.get(url__startswith=f"{POKEAPI}/ability/").respond(404)
        router.get(url__startswith=SPRITES).respond(
            content=artwork_png(), headers={"Content-Type": "image/png"}
        )
        yield router


@pytest.fixture
def pokedex(session: Session, test_user: dict) -> dict[int, int]:
    """Pokédex de ash con todos los Pokémon de KNOWN. Devuelve {pokemon_id: entry_id}."""
    ids = {}
    for pid, (name, types) in KNOWN.items():
        entry = PokedexEntry(
            owner_id=test_user["id"],
            pokemon_id=pid,
            pokemon_name=name,
            pokemon_types=list(types),
            pokemon_sprite=f"{SPRITES}/{pid}.png",
            nickname="Sparky" if pid == 25 else None,
        )
        session.add(entry)
        session.commit()
        ids[pid] = entry.id
    return ids


@pytest.fixture
def create(client: TestClient, auth_headers: dict, pokedex):
    def _create(pokemon_ids: list[int], name: str = "Equipo Élite", expected: int = 201, **extra):
        response = client.post(
            TEAMS, json={"name": name, "pokemon_ids": pokemon_ids, **extra}, headers=auth_headers
        )
        assert response.status_code == expected, response.text
        return response.json()

    return _create


@pytest.fixture
def gary_headers(client: TestClient) -> dict[str, str]:
    client.post(
        "/api/v1/auth/register",
        json={"username": "gary", "email": "gary@kanto.dev", "password": "Eevee1234"},
    )
    token = client.post(
        "/api/v1/auth/login", data={"username": "gary", "password": "Eevee1234"}
    ).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


# ═════════════════════════════════════════════════════════════
#  Crear
# ═════════════════════════════════════════════════════════════
def test_create_team_with_six_pokemon(create) -> None:
    team = create(
        [1, 4, 7, 25, 133, 143], description="Mi equipo principal", name="  Equipo   Élite "
    )

    assert team["name"] == "Equipo Élite"
    assert team["description"] == "Mi equipo principal"
    assert [(m["position"], m["pokemon_id"]) for m in team["members"]] == [
        (1, 1), (2, 4), (3, 7), (4, 25), (5, 133), (6, 143)
    ]  # fmt: skip
    pikachu = team["members"][3]
    assert pikachu["nickname"] == "Sparky"
    assert pikachu["types"] == ["electric"]
    assert pikachu["sprite"].endswith("/25.png")


def test_team_cannot_have_more_than_six(create) -> None:
    create([1, 4, 6, 7, 25, 133, 143], expected=422)


def test_team_cannot_repeat_a_pokemon(create) -> None:
    create([25, 25], expected=422)


@pytest.mark.parametrize("ids", [[], [0], [-3]])
def test_team_ids_must_be_valid(create, ids) -> None:
    create(ids, expected=422)


def test_all_members_must_be_in_my_pokedex(create) -> None:
    detail = create([25, 9, 151], expected=422)["detail"]

    assert detail == "Estos Pokémon no están en tu Pokédex: [9, 151]. Añádelos primero."


@pytest.mark.parametrize("name", ["", "   ", "x" * 101])
def test_team_name_is_validated(create, name: str) -> None:
    create([25], name=name, expected=422)


def test_names_are_unique_ignoring_case_and_accents_casing(create) -> None:
    create([25], name="Equipo Élite")

    assert create([4], name="EQUIPO ÉLITE", expected=409)["detail"] == (
        "Ya tienes un equipo con ese nombre."
    )


def test_teams_per_user_are_limited(create, session: Session, test_user: dict) -> None:
    for i in range(MAX_TEAMS_PER_USER):
        session.add(Team(name=f"Equipo {i}", trainer_id=test_user["id"]))
    session.commit()

    create([25], name="Uno más", expected=409)


def test_teams_require_authentication(client: TestClient) -> None:
    assert client.get(TEAMS).status_code == 401
    assert client.post(TEAMS, json={"name": "x", "pokemon_ids": [1]}).status_code == 401


# ═════════════════════════════════════════════════════════════
#  Listar, consultar, actualizar y borrar
# ═════════════════════════════════════════════════════════════
def test_list_my_teams(client: TestClient, auth_headers: dict, create, gary_headers) -> None:
    create([25], name="Eléctricos")
    create([4, 6], name="Fuego")

    teams = client.get(TEAMS, headers=auth_headers).json()

    assert [t["name"] for t in teams] == ["Eléctricos", "Fuego"]
    assert client.get(TEAMS, headers=gary_headers).json() == []


def test_get_one_team(client: TestClient, auth_headers: dict, create) -> None:
    team = create([25, 4])

    assert client.get(f"{TEAMS}/{team['id']}", headers=auth_headers).json() == team


def test_update_name_description_and_members(
    client: TestClient, auth_headers: dict, create
) -> None:
    team = create([1, 4, 7])

    response = client.put(
        f"{TEAMS}/{team['id']}",
        json={"name": "Nuevo nombre", "description": "Otra idea", "pokemon_ids": [25, 7]},
        headers=auth_headers,
    )

    assert response.status_code == 200
    body = response.json()
    assert (body["name"], body["description"]) == ("Nuevo nombre", "Otra idea")
    assert [(m["position"], m["pokemon_id"]) for m in body["members"]] == [(1, 25), (2, 7)]
    assert body["updated_at"] >= team["updated_at"]


def test_partial_update_keeps_the_rest(client: TestClient, auth_headers: dict, create) -> None:
    team = create([1, 4, 7], description="Descripción")

    body = client.put(
        f"{TEAMS}/{team['id']}", json={"name": "Solo cambio el nombre"}, headers=auth_headers
    ).json()

    assert body["description"] == "Descripción"
    assert [m["pokemon_id"] for m in body["members"]] == [1, 4, 7]


def test_update_can_reorder_members(client: TestClient, auth_headers: dict, create) -> None:
    team = create([1, 4, 7])

    body = client.put(
        f"{TEAMS}/{team['id']}", json={"pokemon_ids": [7, 1, 4]}, headers=auth_headers
    ).json()

    assert [m["pokemon_id"] for m in body["members"]] == [7, 1, 4]


@pytest.mark.parametrize(
    "body",
    [
        {"name": None},
        {"name": "   "},
        {"pokemon_ids": [1, 2, 3, 4, 5, 6, 7]},
        {"pokemon_ids": [25, 25]},
        {"pokemon_ids": [999]},
    ],
)
def test_invalid_updates_are_rejected(
    client: TestClient, auth_headers: dict, create, body: dict
) -> None:
    team = create([25])

    assert client.put(f"{TEAMS}/{team['id']}", json=body, headers=auth_headers).status_code == 422
    assert client.get(f"{TEAMS}/{team['id']}", headers=auth_headers).json() == team


def test_rename_to_another_existing_team_is_rejected(
    client: TestClient, auth_headers: dict, create
) -> None:
    create([25], name="Primero")
    second = create([4], name="Segundo")

    url = f"{TEAMS}/{second['id']}"
    assert client.put(url, json={"name": "primero"}, headers=auth_headers).status_code == 409
    assert client.put(url, json={"name": "SEGUNDO"}, headers=auth_headers).status_code == 200


def test_delete_team_keeps_the_pokedex(
    client: TestClient, auth_headers: dict, create, session: Session
) -> None:
    team = create([25, 4])

    assert client.delete(f"{TEAMS}/{team['id']}", headers=auth_headers).status_code == 204
    assert client.get(f"{TEAMS}/{team['id']}", headers=auth_headers).status_code == 404
    assert session.exec(select(TeamMember)).all() == []
    assert len(session.exec(select(PokedexEntry)).all()) == len(KNOWN)


def test_other_users_teams_are_invisible(
    client: TestClient, auth_headers: dict, create, gary_headers: dict, api
) -> None:
    team = create([25])
    url = f"{TEAMS}/{team['id']}"

    assert client.get(url, headers=gary_headers).status_code == 404
    assert client.put(url, json={"name": "Robado"}, headers=gary_headers).status_code == 404
    assert client.delete(url, headers=gary_headers).status_code == 404
    assert client.get(f"{url}/analysis", headers=gary_headers).status_code == 404
    assert client.get(f"{url}/export", headers=gary_headers).status_code == 404
    assert client.get(url, headers=auth_headers).json()["name"] == "Equipo Élite"


def test_releasing_a_pokemon_removes_it_from_the_team(
    client: TestClient, auth_headers: dict, create, pokedex: dict
) -> None:
    team = create([25, 4])

    client.delete(f"/api/v1/pokedex/{pokedex[25]}", headers=auth_headers)

    members = client.get(f"{TEAMS}/{team['id']}", headers=auth_headers).json()["members"]
    assert [m["pokemon_id"] for m in members] == [4]


# ═════════════════════════════════════════════════════════════
#  Análisis y exportación
# ═════════════════════════════════════════════════════════════
def test_team_analysis(client: TestClient, auth_headers: dict, create, api) -> None:
    team = create([4, 37, 58], name="Todo fuego")

    response = client.get(f"{TEAMS}/{team['id']}/analysis", headers=auth_headers)

    assert response.status_code == 200
    body = response.json()
    assert body["combined"]["total"]["hp"] == 30  # 10 cada uno en los datos falsos
    assert body["combined"]["average"]["speed"] == 60
    assert body["combined"]["base_stat_total"] == 3 * 210
    # Tres Pokémon de fuego: agua, tierra y roca son debilidades compartidas.
    assert set(body["shared_weaknesses"]) == {"water", "ground", "rock"}
    assert body["defensive"][0]["type"] in {"water", "ground", "rock"}
    assert "grass" in body["super_effective_against"]
    assert "water" in body["not_covered"]
    assert any("Debilidades compartidas" in h for h in body["highlights"])


def test_export_team_pdf(client: TestClient, auth_headers: dict, create, api) -> None:
    team = create([6, 25, 94, 150, 143, 7], description="Mi equipo principal <b>&</b>")

    response = client.get(f"{TEAMS}/{team['id']}/export", headers=auth_headers)

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["content-disposition"].startswith(
        'attachment; filename="equipo-equipo-elite-'
    )
    assert response.content.startswith(b"%PDF")
    assert response.content.count(b"/Type /Page\n") >= 3  # resumen, fichas y cobertura


def test_export_when_pokeapi_is_down_uses_fallback(
    client: TestClient, auth_headers: dict, create
) -> None:
    async def no_sleep(_: float) -> None:
        return None

    client.app.state.pokeapi._sleep = no_sleep
    team = create([25, 150])
    with respx.mock() as router:
        router.route().mock(side_effect=httpx.ConnectTimeout("caída"))
        response = client.get(f"{TEAMS}/{team['id']}/export", headers=auth_headers)

    assert response.status_code == 200
    assert response.content.startswith(b"%PDF")


# ═════════════════════════════════════════════════════════════
#  Tabla de tipos y análisis (funciones puras)
# ═════════════════════════════════════════════════════════════
@pytest.mark.parametrize(
    ("attacking", "defending", "expected"),
    [
        ("electric", ["water", "flying"], 4),  # Gyarados
        ("ground", ["electric", "flying"], 0),  # Zapdos
        ("normal", ["ghost"], 0),
        ("fire", ["grass", "steel"], 4),  # Ferrothorn
        ("dragon", ["fairy"], 0),
        ("water", ["fire", "ground"], 4),
        ("fighting", ["normal", "flying"], 1),  # x2 por normal, x0,5 por volador
        ("psychic", ["dark"], 0),
        ("ice", ["dragon", "flying"], 4),  # Dragonite
    ],
)
def test_type_effectiveness(attacking: str, defending: list[str], expected: float) -> None:
    assert effectiveness(attacking, defending) == expected


def test_defensive_matchups_are_sorted_by_danger() -> None:
    matchups = defensive_matchups([["fire"], ["fire"], ["fire", "flying"]])

    assert matchups[0].type == "rock"  # x2, x2 y x4
    assert matchups[0].weak == 3
    assert matchups[0].multipliers == [2, 2, 4]


def test_offensive_coverage() -> None:
    covered = offensive_coverage([["electric"]])

    assert covered == ["water", "flying"]


def make_detail(pid: int, types: list[str], speed: int = 50) -> PokemonDetail:
    return PokemonDetail(
        id=pid,
        name=f"pokemon-{pid}",
        height=10,
        weight=100,
        types=types,
        abilities=[PokemonAbility(name="x")],
        stats=PokemonStats(
            hp=50, attack=60, defense=70, special_attack=80, special_defense=90, speed=speed
        ),
        sprites=PokemonSprites(),
    )


def test_analysis_highlights() -> None:
    analysis = analyze_team(
        1, "Mixto", [make_detail(1, ["water"], speed=40), make_detail(2, ["grass"], speed=99)]
    )

    assert analysis.highlights[0] == (
        "Punto fuerte: Defensa especial (media 90). Punto débil: PS (media 50)."
    )
    assert analysis.highlights[1] == "El más rápido es Pokemon-2 (99 de velocidad)."
    assert "Quedan 4 huecos libres en el equipo." in analysis.highlights


def test_analysis_of_an_empty_team() -> None:
    analysis = analyze_team(1, "Vacío", [])

    assert analysis.highlights == ["El equipo está vacío."]
    assert analysis.combined.base_stat_total == 0


def test_spanish_number_format() -> None:
    assert es_number(66.3) == "66,3"
    assert es_number(93.0) == "93"


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Equipo Élite", "equipo-elite"),
        ("¡Ñandú  rápido!", "nandu-rapido"),
        ("🔥🔥🔥", "7"),
        ('"; rm -rf /', "rm-rf"),
    ],
)
def test_download_names_are_ascii(name: str, expected: str) -> None:
    assert _ascii_slug(name, fallback="7") == expected
