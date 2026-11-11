"""API de combates, experiencia, niveles y evolución."""

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.models import PokedexEntry, PokemonProgress
from app.routers import battles as battles_router
from tests.fakes import pokemon_payload, with_real_sprites

BATTLES = "/api/v1/battles"


def start(client: TestClient, headers: dict, team_id: int, **extra) -> dict:
    response = client.post(BATTLES, json={"team_id": team_id, **extra}, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


def play_to_the_end(client: TestClient, headers: dict, battle: dict) -> dict:
    for _ in range(200):
        if battle["status"] != "active":
            return battle
        move = max(battle["actions"]["moves"], key=lambda m: m["effectiveness"] * m["power"])
        battle = client.post(
            f"{BATTLES}/{battle['id']}/turn",
            json={"type": "move", "index": move["index"]},
            headers=headers,
        ).json()
    raise AssertionError("el combate no termina")


@pytest.fixture(autouse=True)
def fixed_seed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Semilla fija: los tests de la API no dependen de lo que salga al azar (un combate con
    dos Pokémon inmunes entre sí duraría el máximo de turnos)."""
    monkeypatch.setattr(battles_router.secrets, "randbits", lambda _bits: 20261111)


@pytest.fixture
def no_evolutions(monkeypatch: pytest.MonkeyPatch) -> None:
    async def none(*_):
        return None

    monkeypatch.setattr(battles_router, "level_evolution", none)


def test_start_a_battle(client: TestClient, auth_headers: dict, api, fire_team: int) -> None:
    battle = start(client, auth_headers, fire_team, difficulty="easy", theme="water")

    assert battle["status"] == "active"
    assert battle["turn"] == 0
    assert [f["name"] for f in battle["you"]["fighters"]] == ["charmander", "vulpix", "growlithe"]
    assert all(f["level"] == 10 for f in battle["you"]["fighters"])  # nivel por defecto
    assert len(battle["rival"]["fighters"]) == 3
    assert all("water" in f["types"] for f in battle["rival"]["fighters"])
    assert all(f["level"] in (6, 7, 8) for f in battle["rival"]["fighters"])  # fácil: 3 menos
    assert battle["actions"]["moves"][0]["name"] == "Llamarada ígnea"
    assert battle["actions"]["switch"] == [1, 2]


def test_play_until_the_end_and_gain_experience(
    client: TestClient, auth_headers: dict, session: Session, api, fire_team: int, no_evolutions
) -> None:
    battle = play_to_the_end(
        client, auth_headers, start(client, auth_headers, fire_team, difficulty="easy")
    )

    assert battle["status"] in {"won", "lost", "draw"}
    assert battle["actions"] is None
    assert battle["events"][-1]["type"] == "end"
    rewards = battle["rewards"]
    assert rewards and all(r["xp_gained"] > 0 for r in rewards)
    progress = {p.entry_id: p for p in session.exec(select(PokemonProgress)).all()}
    for reward in rewards:
        stored = progress[reward["entry_id"]]
        assert stored.battles == 1
        assert stored.level == reward["level_after"] >= reward["level_before"]

    levels = client.get("/api/v1/progress", headers=auth_headers).json()
    assert {p["entry_id"] for p in levels} >= {r["entry_id"] for r in rewards}
    assert all(p["xp_to_next"] == 4 * p["level"] + 8 for p in levels)


def test_rewards_are_given_only_once(
    client: TestClient, auth_headers: dict, session: Session, api, fire_team: int, no_evolutions
) -> None:
    battle = play_to_the_end(
        client, auth_headers, start(client, auth_headers, fire_team, difficulty="easy")
    )

    again = client.post(
        f"{BATTLES}/{battle['id']}/turn", json={"type": "move", "index": 0}, headers=auth_headers
    )
    assert again.status_code == 409
    assert all(p.battles == 1 for p in session.exec(select(PokemonProgress)).all())


def test_state_is_rebuilt_from_the_actions(
    client: TestClient, auth_headers: dict, api, fire_team: int
) -> None:
    battle = start(client, auth_headers, fire_team)
    after = client.post(
        f"{BATTLES}/{battle['id']}/turn", json={"type": "move", "index": 0}, headers=auth_headers
    ).json()

    again = client.get(f"{BATTLES}/{battle['id']}", headers=auth_headers).json()

    assert again == after  # el mismo estado y los mismos sucesos, reconstruidos


def test_invalid_actions(client: TestClient, auth_headers: dict, api, fire_team: int) -> None:
    battle = start(client, auth_headers, fire_team)
    url = f"{BATTLES}/{battle['id']}/turn"

    assert (
        client.post(url, json={"type": "switch", "to": 0}, headers=auth_headers).status_code == 422
    )
    assert (
        client.post(url, json={"type": "move", "index": 3}, headers=auth_headers).status_code == 422
    )
    assert client.post(url, json={"type": "jump"}, headers=auth_headers).status_code == 422


def test_forfeit_gives_no_experience(
    client: TestClient, auth_headers: dict, session: Session, api, fire_team: int
) -> None:
    battle = start(client, auth_headers, fire_team)

    body = client.post(f"{BATTLES}/{battle['id']}/forfeit", headers=auth_headers).json()

    assert body["status"] == "forfeit"
    assert body["rewards"] == []
    assert session.exec(select(PokemonProgress)).all() == []


def test_someone_elses_battle_is_404(
    client: TestClient, auth_headers: dict, api, fire_team: int
) -> None:
    battle = start(client, auth_headers, fire_team)
    other = client.post(
        "/api/v1/auth/register",
        json={"username": "gary", "email": "gary@kanto.dev", "password": "Eevee1234"},
    )
    assert other.status_code == 201
    token = client.post(
        "/api/v1/auth/login", data={"username": "gary", "password": "Eevee1234"}
    ).json()
    gary = {"Authorization": f"Bearer {token['access_token']}"}

    assert client.get(f"{BATTLES}/{battle['id']}", headers=gary).status_code == 404
    assert (
        client.post(
            f"{BATTLES}/{battle['id']}/turn", json={"type": "move", "index": 0}, headers=gary
        ).status_code
        == 404
    )


def test_history(client: TestClient, auth_headers: dict, api, fire_team: int) -> None:
    first = start(client, auth_headers, fire_team)
    second = start(client, auth_headers, fire_team)

    history = client.get(BATTLES, headers=auth_headers).json()

    assert [b["id"] for b in history] == [second["id"], first["id"]]
    assert history[0]["status"] == "active"


def test_empty_or_foreign_team(client: TestClient, auth_headers: dict, api) -> None:
    assert client.post(BATTLES, json={"team_id": 999}, headers=auth_headers).status_code == 404


# ═════════════════════════════════════════════════════════════
#  Evolución por nivel
# ═════════════════════════════════════════════════════════════
@pytest.fixture
def charmander_evolves(monkeypatch: pytest.MonkeyPatch, client: TestClient) -> None:
    async def evolution(_pokeapi, pokemon_id: int):
        return (5, "charmeleon", 16) if pokemon_id == 4 else None

    monkeypatch.setattr(battles_router, "level_evolution", evolution)
    pokeapi = client.app.state.pokeapi
    original = pokeapi.get_pokemon

    async def get_pokemon(id_or_name):
        if str(id_or_name) == "5":
            from app.services.pokeapi_service import to_pokemon_detail

            return to_pokemon_detail(with_real_sprites(pokemon_payload(5, "charmeleon", ("fire",))))
        return await original(id_or_name)

    monkeypatch.setattr(pokeapi, "get_pokemon", get_pokemon)


def charmander_entry(session: Session) -> PokedexEntry:
    return session.exec(select(PokedexEntry).where(PokedexEntry.pokemon_id == 4)).one()


def test_evolve_when_the_level_is_reached(
    client: TestClient,
    auth_headers: dict,
    session: Session,
    api,
    fire_team: int,
    charmander_evolves,
) -> None:
    entry = charmander_entry(session)
    url = f"/api/v1/pokedex/{entry.id}/evolve"
    assert (
        client.post(url, headers=auth_headers).json()["detail"]
        == "Necesita llegar al nivel 16 (está en el 10)."
    )

    session.add(PokemonProgress(entry_id=entry.id, level=16))
    session.commit()
    response = client.post(url, headers=auth_headers)

    assert response.status_code == 200
    body = response.json()
    assert (body["id"], body["pokemon_id"], body["pokemon_name"]) == (entry.id, 5, "charmeleon")
    assert body["nickname"] == entry.nickname  # conserva apodo y equipos (mismo id de entrada)


def test_no_evolution_if_you_already_have_it(
    client: TestClient,
    auth_headers: dict,
    session: Session,
    test_user: dict,
    api,
    fire_team: int,
    charmander_evolves,
) -> None:
    entry = charmander_entry(session)
    session.add(PokemonProgress(entry_id=entry.id, level=20))
    session.add(
        PokedexEntry(
            owner_id=test_user["id"],
            pokemon_id=5,
            pokemon_name="charmeleon",
            pokemon_types=["fire"],
        )
    )
    session.commit()

    response = client.post(f"/api/v1/pokedex/{entry.id}/evolve", headers=auth_headers)

    assert response.status_code == 409
    assert "Ya tienes" in response.json()["detail"]


def test_pokemon_without_level_evolution(
    client: TestClient,
    auth_headers: dict,
    session: Session,
    api,
    fire_team: int,
    charmander_evolves,
) -> None:
    vulpix = session.exec(select(PokedexEntry).where(PokedexEntry.pokemon_id == 37)).one()

    response = client.post(f"/api/v1/pokedex/{vulpix.id}/evolve", headers=auth_headers)

    assert response.status_code == 409
    assert client.post("/api/v1/pokedex/9999/evolve", headers=auth_headers).status_code == 404


def test_rewards_announce_when_a_pokemon_can_evolve(
    client: TestClient, auth_headers: dict, session: Session, api, fire_team: int, monkeypatch
) -> None:
    async def evolution(_pokeapi, pokemon_id: int):
        return (5, "charmeleon", 11) if pokemon_id == 4 else None

    monkeypatch.setattr(battles_router, "level_evolution", evolution)
    entry = charmander_entry(session)
    session.add(PokemonProgress(entry_id=entry.id, level=10, xp=47))  # a 1 punto del 11
    session.commit()

    battle = play_to_the_end(
        client, auth_headers, start(client, auth_headers, fire_team, difficulty="easy")
    )

    reward = next(r for r in battle["rewards"] if r["entry_id"] == entry.id)
    assert reward["level_after"] >= 11
    assert reward["evolves_to"] == "charmeleon"
