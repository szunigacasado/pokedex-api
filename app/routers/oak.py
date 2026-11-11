"""Endpoints del «Profesor Oak» (IA con respaldo por reglas)."""

from typing import Annotated

from fastapi import APIRouter, Query, Request, Response
from sqlmodel import select

from app import rate_limit as rl
from app.dependencies import CurrentUser, PokeAPIDep, SessionDep
from app.models import MAX_TEAM_SIZE, AskResult, PokedexEntry, TeamAdvice, TeamRecommendations
from app.rate_limit import limiter
from app.routers.teams import TeamId, _members_detail, _owned_team, _to_read
from app.services.ai_oak import MAX_QUESTION_LENGTH, OakAI, explain_query, run_query
from app.services.recommendations import recommend
from app.services.team_analysis import analyze_team

router = APIRouter(tags=["Profesor Oak (IA)"])

KANTO_DATASET = "Pokédex de Kanto (151 Pokémon, datos de PokeAPI)"


def _oak(request: Request) -> OakAI:
    return request.app.state.oak


@router.get(
    "/teams/{team_id}/advice",
    response_model=TeamAdvice,
    summary="Consejo del Profesor Oak para un equipo",
    responses={404: {"description": "No existe o no es tuyo"}},
)
@limiter.limit(rl.AI)
async def team_advice(
    request: Request,
    response: Response,
    team_id: TeamId,
    user: CurrentUser,
    session: SessionDep,
    pokeapi: PokeAPIDep,
) -> TeamAdvice:
    """Consejo estratégico redactado por IA **a partir del análisis ya calculado** (tabla
    de tipos y estadísticas). El modelo no recibe el nombre, la descripción ni los apodos
    del equipo, solo datos de PokeAPI, así que no se le pueden colar instrucciones.

    Sin clave de Groq, o si la IA falla, el consejo se genera con reglas
    (`source: "rules"`).
    """
    team = _to_read(_owned_team(session, user, team_id))
    analysis = analyze_team(team.id, team.name, await _members_detail(pokeapi, team))
    advice, model = await _oak(request).advise_team(analysis)
    return TeamAdvice(
        **advice.model_dump(),
        team_id=team.id,
        source="ai" if model else "rules",
        model=model,
    )


@router.get(
    "/teams/{team_id}/recommendations",
    response_model=TeamRecommendations,
    summary="Pokémon recomendados para mejorar un equipo",
    responses={404: {"description": "No existe o no es tuyo"}},
)
@limiter.limit(rl.EXTERNAL)
async def team_recommendations(
    request: Request,
    response: Response,
    team_id: TeamId,
    user: CurrentUser,
    session: SessionDep,
    pokeapi: PokeAPIDep,
) -> TeamRecommendations:
    """Pokémon **concretos** de Kanto que arreglan los puntos débiles del equipo, con el
    motivo de cada uno («Resiste Eléctrico (×½)»).

    - **Defensa:** para cada tipo de ataque que hace daño doble a más miembros de los que
      lo resisten, Pokémon que lo resisten o son inmunes.
    - **Ataque:** para cada tipo al que el equipo no golpea con daño doble, Pokémon cuyo
      tipo sí lo hace.

    Se calcula con la tabla de tipos, sin IA. Primero salen los que ya están en tu
    Pokédex (`in_pokedex`), que puedes meter en el equipo al momento.
    """
    team = _to_read(_owned_team(session, user, team_id))
    analysis = analyze_team(team.id, team.name, await _members_detail(pokeapi, team))
    owned = dict(
        session.exec(
            select(PokedexEntry.pokemon_id, PokedexEntry.is_captured).where(
                PokedexEntry.owner_id == user.id
            )
        ).all()
    )
    return TeamRecommendations(
        team_id=team.id,
        free_slots=MAX_TEAM_SIZE - len(team.members),
        recommendations=recommend(analysis, pokeapi.fallback.all_pokemon(), owned),
    )


@router.get(
    "/pokemon/ask",
    response_model=AskResult,
    summary="Buscar Pokémon en lenguaje natural",
)
@limiter.limit(rl.AI)
async def ask(
    request: Request,
    response: Response,
    _: CurrentUser,
    pokeapi: PokeAPIDep,
    q: Annotated[
        str,
        Query(
            min_length=3,
            max_length=MAX_QUESTION_LENGTH,
            description="Lo que buscas, en español.",
            examples=["Pokémon de agua rápidos", "los 5 legendarios más fuertes"],
        ),
    ],
) -> AskResult:
    """Escribe lo que buscas como lo dirías («los 3 Pokémon de fuego más rápidos», «tipo
    planta sin veneno con más de 80 de defensa»…).

    La IA solo **traduce** la frase a filtros (`interpreted`) de una lista cerrada, y la
    búsqueda la hace el código sobre los datos reales de los 151 Pokémon de Kanto. Así
    nunca devuelve un Pokémon o una estadística inventados. `explanation` describe en
    español los filtros aplicados.
    """
    oak = _oak(request)
    query, model = await oak.interpret(q)
    fallback = pokeapi.fallback
    legendary, mythical = fallback.special_ids()
    count, results = run_query(query, fallback.all_pokemon(), legendary, mythical)
    return AskResult(
        question=q,
        interpreted=query,
        explanation=explain_query(query),
        dataset=KANTO_DATASET,
        count=count,
        results=results,
        source="ai" if model else "rules",
        model=model,
    )
