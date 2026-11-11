"""Endpoints del «Profesor Oak» (IA con respaldo por reglas)."""

from typing import Annotated

from fastapi import APIRouter, Query, Request, Response

from app import rate_limit as rl
from app.dependencies import CurrentUser, PokeAPIDep, SessionDep
from app.models import AskResult, TeamAdvice
from app.rate_limit import limiter
from app.routers.teams import TeamId, _members_detail, _owned_team, _to_read
from app.services.ai_oak import MAX_QUESTION_LENGTH, OakAI, explain_query, run_query
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
