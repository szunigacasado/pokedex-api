"""Recomendaciones del Profesor Oak: Pokémon concretos para mejorar un equipo.

Las expectativas se calculan con la propia tabla de tipos: cada Pokémon propuesto debe
arreglar de verdad el problema para el que se propone."""

from fastapi.testclient import TestClient
from sqlmodel import Session

from app.models import PokedexEntry
from app.services.pokeapi_service import FallbackData
from app.services.recommendations import multiplier_label, recommend
from app.services.team_analysis import analyze_team, effectiveness

KANTO = {p.id: p for p in FallbackData().all_pokemon()}
FIRE_TEAM = (4, 37, 58)  # Charmander, Vulpix y Growlithe: débiles a Agua, Tierra y Roca


def team_of(*ids: int):
    return analyze_team(1, "Prueba", [KANTO[i] for i in ids])


def total(pokemon_id: int) -> int:
    return sum(KANTO[pokemon_id].stats.model_dump().values())


def weakest_that_resists(attacking: str) -> int:
    """El Pokémon más débil de los que resisten: sin tenerlo, nunca saldría el primero."""
    candidates = (
        pid
        for pid, p in KANTO.items()
        if pid not in FIRE_TEAM and effectiveness(attacking, p.types) < 1
    )
    return min(candidates, key=total)


def test_multiplier_labels() -> None:
    assert [multiplier_label(v) for v in (0.25, 0.5, 0, 2, 4)] == ["×¼", "×½", "×0", "×2", "×4"]


def test_defensive_recommendations_really_resist() -> None:
    analysis = team_of(*FIRE_TEAM)
    recs = recommend(analysis, KANTO.values(), owned={})

    defense = [r for r in recs if r.kind == "defense"]
    assert len(defense) == 2  # los dos problemas más graves
    for rec in defense:
        problem = next(m for m in analysis.defensive if m.type == rec.target_type)
        assert problem.weak > problem.resist + problem.immune  # es un problema real
        assert rec.title.startswith("Cubrir la debilidad a ")
        assert "3 de 3 miembros" in rec.detail
        assert rec.pokemon
        for p in rec.pokemon:
            assert effectiveness(rec.target_type, KANTO[p.id].types) < 1
            assert p.reason.startswith(("Resiste", "Inmune"))
        for t in rec.useful_types:
            assert effectiveness(rec.target_type, [t]) < 1


def test_offense_recommendations_really_hit_the_target() -> None:
    recs = recommend(team_of(*FIRE_TEAM), KANTO.values(), owned={})

    offense = [r for r in recs if r.kind == "offense"]
    assert offense
    for rec in offense:
        assert rec.title.startswith("Poder golpear a ")
        for p in rec.pokemon:
            assert any(effectiveness(t, [rec.target_type]) > 1 for t in KANTO[p.id].types)
            assert "hace ×2" in p.reason


def test_team_members_are_never_recommended_and_goals_are_capped() -> None:
    recs = recommend(team_of(*FIRE_TEAM), KANTO.values(), owned={})

    assert 1 <= len(recs) <= 4
    assert not {p.id for r in recs for p in r.pokemon} & set(FIRE_TEAM)
    assert all(len(r.pokemon) <= 4 for r in recs)


def test_strongest_first_when_nothing_is_owned() -> None:
    for rec in recommend(team_of(*FIRE_TEAM), KANTO.values(), owned={}):
        totals = [p.total for p in rec.pokemon]
        assert totals == sorted(totals, reverse=True)


def test_pokemon_already_in_the_pokedex_come_first() -> None:
    first = recommend(team_of(*FIRE_TEAM), KANTO.values(), owned={})[0]
    weakest = weakest_that_resists(first.target_type)

    rec = recommend(team_of(*FIRE_TEAM), KANTO.values(), owned={weakest: False})[0]

    assert rec.pokemon[0].id == weakest
    assert rec.pokemon[0].in_pokedex
    assert not rec.pokemon[0].captured
    assert not any(p.in_pokedex for p in rec.pokemon[1:])


def test_types_the_team_already_resists_are_not_problems() -> None:
    analysis = team_of(6, 9, 3, 25, 143, 94)

    for rec in recommend(analysis, KANTO.values(), owned={}):
        if rec.kind == "defense":
            m = next(m for m in analysis.defensive if m.type == rec.target_type)
            assert m.weak > m.resist + m.immune


# ═════════════════════════════════════════════════════════════
#  Endpoint
# ═════════════════════════════════════════════════════════════
def test_recommendations_endpoint(
    client: TestClient,
    auth_headers: dict,
    session: Session,
    test_user: dict,
    api,
    fire_team: int,
) -> None:
    url = f"/api/v1/teams/{fire_team}/recommendations"
    first = client.get(url, headers=auth_headers).json()["recommendations"][0]
    p = KANTO[weakest_that_resists(first["target_type"])]
    session.add(
        PokedexEntry(
            owner_id=test_user["id"],
            pokemon_id=p.id,
            pokemon_name=p.name,
            pokemon_types=p.types,
            is_captured=True,
        )
    )
    session.commit()

    response = client.get(url, headers=auth_headers)

    assert response.status_code == 200
    body = response.json()
    assert body["free_slots"] == 3
    top = body["recommendations"][0]["pokemon"][0]
    assert top["id"] == p.id
    assert top["in_pokedex"] is True
    assert top["captured"] is True
    assert top["total"] == total(p.id)
    assert top["reason"].startswith(("Resiste", "Inmune"))


def test_recommendations_of_someone_elses_team_is_404(
    client: TestClient, auth_headers: dict, api
) -> None:
    assert client.get("/api/v1/teams/999/recommendations", headers=auth_headers).status_code == 404
