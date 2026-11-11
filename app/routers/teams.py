"""Equipos de batalla: hasta seis Pokémon de la Pokédex del usuario."""

import asyncio
import logging
import unicodedata
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Request, Response, status
from sqlalchemy import func
from sqlmodel import Session, select
from starlette.concurrency import run_in_threadpool

from app import rate_limit as rl
from app.dependencies import CurrentUser, PokeAPIDep, SessionDep
from app.models import (
    PokedexEntry,
    PokemonDetail,
    Team,
    TeamAnalysis,
    TeamCreate,
    TeamMember,
    TeamMemberRead,
    TeamRead,
    TeamUpdate,
    User,
    utc_now,
)
from app.rate_limit import limiter
from app.services.card_renderer import encode_card, render_card
from app.services.exporters import team_to_pdf
from app.services.pokeapi_service import PokeAPIError, PokeAPIService
from app.services.team_analysis import analyze_team

logger = logging.getLogger("pokedex_api.teams")

router = APIRouter(prefix="/teams", tags=["Equipos"])

MAX_TEAMS_PER_USER = 20
NOT_FOUND = "Equipo no encontrado."
TeamId = Annotated[int, Path(ge=1, description="ID del equipo.")]


# ═════════════════════════════════════════════════════════════
#  Utilidades
# ═════════════════════════════════════════════════════════════
def _owned_team(session: Session, user: User, team_id: int) -> Team:
    team = session.exec(select(Team).where(Team.id == team_id, Team.trainer_id == user.id)).first()
    if team is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=NOT_FOUND)
    return team


def _entries_for(session: Session, user: User, pokemon_ids: Sequence[int]) -> list[PokedexEntry]:
    """Entradas de la Pokédex del usuario para esos Pokémon, en el mismo orden.

    Responde 422 indicando cuáles faltan si alguno no está en su Pokédex.
    """
    found = {
        e.pokemon_id: e
        for e in session.exec(
            select(PokedexEntry).where(
                PokedexEntry.owner_id == user.id,
                PokedexEntry.pokemon_id.in_(pokemon_ids),  # type: ignore[attr-defined]
            )
        ).all()
    }
    missing = [pid for pid in pokemon_ids if pid not in found]
    if missing:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Estos Pokémon no están en tu Pokédex: {missing}. Añádelos primero.",
        )
    return [found[pid] for pid in pokemon_ids]


def _check_name_is_free(session: Session, user: User, name: str, exclude_id: int | None) -> None:
    # La comparación se hace en Python con casefold(): el lower() de SQLite solo convierte
    # letras ASCII, así que «Equipo Élite» y «equipo élite» le parecerían distintos.
    query = select(Team.id, Team.name).where(Team.trainer_id == user.id)
    if exclude_id is not None:
        query = query.where(Team.id != exclude_id)
    if any(existing.casefold() == name.casefold() for _, existing in session.exec(query).all()):
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Ya tienes un equipo con ese nombre.")


def _ascii_slug(text: str, fallback: str) -> str:
    """Nombre de archivo seguro para la cabecera HTTP: ASCII, sin espacios ni símbolos."""
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    slug = "".join(c if c.isalnum() else "-" for c in ascii_text.lower())
    slug = "-".join(part for part in slug.split("-") if part)[:40]
    return slug or fallback


def _replace_members(session: Session, team: Team, entries: Sequence[PokedexEntry]) -> None:
    # Los miembros antiguos se sacan también de la colección: si solo se borraran,
    # seguirían en team.members y SQLAlchemy intentaría volver a guardarlos.
    for member in list(team.members):
        team.members.remove(member)
        session.delete(member)
    session.flush()  # libera las posiciones antes de volver a ocuparlas (UNIQUE)
    for position, entry in enumerate(entries, start=1):
        team.members.append(TeamMember(pokedex_entry_id=entry.id, position=position))


def _to_read(team: Team) -> TeamRead:
    return TeamRead(
        id=team.id,
        name=team.name,
        description=team.description,
        created_at=team.created_at,
        updated_at=team.updated_at,
        members=[
            TeamMemberRead(
                position=m.position,
                pokedex_entry_id=m.pokedex_entry_id,
                pokemon_id=m.entry.pokemon_id,
                pokemon_name=m.entry.pokemon_name,
                nickname=m.entry.nickname,
                sprite=m.entry.pokemon_sprite,
                types=m.entry.pokemon_types,
            )
            for m in sorted(team.members, key=lambda m: m.position)
        ],
    )


async def _members_detail(pokeapi: PokeAPIService, team: TeamRead) -> list[PokemonDetail]:
    """Estadísticas de cada miembro desde PokeAPI (con caché y datos de respaldo)."""
    return list(await asyncio.gather(*(pokeapi.get_pokemon(m.pokemon_id) for m in team.members)))


# ═════════════════════════════════════════════════════════════
#  CRUD
# ═════════════════════════════════════════════════════════════
@router.post(
    "",
    response_model=TeamRead,
    status_code=status.HTTP_201_CREATED,
    summary="Crear un equipo",
    responses={
        409: {"description": "Ya existe un equipo con ese nombre o se alcanzó el máximo"},
        422: {"description": "Más de 6 Pokémon, repetidos o que no están en tu Pokédex"},
    },
)
@limiter.limit(rl.DEFAULT)
def create_team(
    request: Request,
    response: Response,
    payload: TeamCreate,
    user: CurrentUser,
    session: SessionDep,
) -> TeamRead:
    """Crea un equipo con hasta 6 Pokémon **de tu Pokédex**, indicados por su ID de
    PokeAPI. El orden de `pokemon_ids` es la posición de cada uno en el equipo."""
    teams = session.exec(select(func.count()).where(Team.trainer_id == user.id)).one()
    if teams >= MAX_TEAMS_PER_USER:
        raise HTTPException(
            status.HTTP_409_CONFLICT, detail=f"Máximo {MAX_TEAMS_PER_USER} equipos por usuario."
        )
    _check_name_is_free(session, user, payload.name, exclude_id=None)
    entries = _entries_for(session, user, payload.pokemon_ids)

    team = Team(name=payload.name, description=payload.description, trainer_id=user.id)
    session.add(team)
    session.flush()
    _replace_members(session, team, entries)
    session.commit()
    session.refresh(team)
    logger.info("Equipos: user_id=%s crea el equipo %s", user.id, team.id)
    return _to_read(team)


@router.get("", response_model=list[TeamRead], summary="Listar mis equipos")
@limiter.limit(rl.DEFAULT)
def list_teams(
    request: Request, response: Response, user: CurrentUser, session: SessionDep
) -> list[TeamRead]:
    teams = session.exec(select(Team).where(Team.trainer_id == user.id).order_by(Team.id)).all()
    return [_to_read(t) for t in teams]


@router.get(
    "/{team_id}",
    response_model=TeamRead,
    summary="Consultar un equipo",
    responses={404: {"description": "No existe o no es tuyo"}},
)
@limiter.limit(rl.DEFAULT)
def get_team(
    request: Request, response: Response, team_id: TeamId, user: CurrentUser, session: SessionDep
) -> TeamRead:
    return _to_read(_owned_team(session, user, team_id))


@router.put(
    "/{team_id}",
    response_model=TeamRead,
    summary="Actualizar un equipo",
    responses={
        404: {"description": "No existe o no es tuyo"},
        409: {"description": "Ya tienes otro equipo con ese nombre"},
        422: {"description": "Más de 6 Pokémon, repetidos o que no están en tu Pokédex"},
    },
)
@limiter.limit(rl.DEFAULT)
def update_team(
    request: Request,
    response: Response,
    team_id: TeamId,
    payload: TeamUpdate,
    user: CurrentUser,
    session: SessionDep,
) -> TeamRead:
    """Actualiza el nombre, la descripción y los miembros.

    Los campos que no se envían se conservan. Si se envía `pokemon_ids`, sustituye a la
    lista completa de miembros, en ese orden.
    """
    team = _owned_team(session, user, team_id)
    changes = payload.model_dump(exclude_unset=True)
    if "name" in changes and changes["name"] is None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, detail="El nombre no puede ser null."
        )

    if changes.get("name"):
        _check_name_is_free(session, user, changes["name"], exclude_id=team.id)
        team.name = changes["name"]
    if "description" in changes:
        team.description = changes["description"]
    if changes.get("pokemon_ids") is not None:
        _replace_members(session, team, _entries_for(session, user, changes["pokemon_ids"]))

    team.updated_at = utc_now()
    session.add(team)
    session.commit()
    session.refresh(team)
    return _to_read(team)


@router.delete(
    "/{team_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Eliminar un equipo",
    responses={404: {"description": "No existe o no es tuyo"}},
)
@limiter.limit(rl.DEFAULT)
def delete_team(
    request: Request, response: Response, team_id: TeamId, user: CurrentUser, session: SessionDep
) -> None:
    """Elimina el equipo. Los Pokémon siguen en tu Pokédex."""
    session.delete(_owned_team(session, user, team_id))
    session.commit()


# ═════════════════════════════════════════════════════════════
#  Análisis y exportación
# ═════════════════════════════════════════════════════════════
@router.get(
    "/{team_id}/analysis",
    response_model=TeamAnalysis,
    summary="Analizar un equipo",
    responses={
        404: {"description": "No existe o no es tuyo"},
        503: {"description": "PokeAPI caída"},
    },
)
@limiter.limit(rl.EXTERNAL)
async def analyze(
    request: Request,
    response: Response,
    team_id: TeamId,
    user: CurrentUser,
    session: SessionDep,
    pokeapi: PokeAPIDep,
) -> TeamAnalysis:
    """Estadísticas combinadas (total y media de cada estadística base) y cobertura de
    tipos con la tabla oficial de efectividades: debilidades compartidas, resistencias y
    tipos a los que el equipo golpea de forma supereficaz.

    Las conclusiones de `highlights` se calculan con reglas a partir de esos datos.
    """
    team = _to_read(_owned_team(session, user, team_id))
    return analyze_team(team.id, team.name, await _members_detail(pokeapi, team))


@router.get(
    "/{team_id}/export",
    summary="Exportar un equipo a PDF",
    response_class=Response,
    responses={
        200: {"content": {"application/pdf": {}}, "description": "PDF descargable"},
        404: {"description": "No existe o no es tuyo"},
        503: {"description": "PokeAPI caída"},
    },
)
@limiter.limit(rl.EXPORT)
async def export_team(
    request: Request, team_id: TeamId, user: CurrentUser, session: SessionDep, pokeapi: PokeAPIDep
) -> Response:
    """PDF del equipo con nombre y descripción, las fichas de sus Pokémon, las
    estadísticas combinadas (con gráfico radar) y la tabla de cobertura de tipos."""
    team = _to_read(_owned_team(session, user, team_id))
    details = await _members_detail(pokeapi, team)
    analysis = analyze_team(team.id, team.name, details)

    async def card_for(detail: PokemonDetail) -> bytes:
        try:
            species = await pokeapi.get_species(detail.species_name or detail.id)
        except PokeAPIError:
            species = None
        artwork, abilities = await asyncio.gather(
            pokeapi.get_image(detail.sprites.official_artwork or detail.sprites.front_default),
            pokeapi.get_ability_names([a.name for a in detail.abilities]),
        )
        image = await run_in_threadpool(render_card, detail, species, artwork, abilities)
        return await run_in_threadpool(encode_card, image, "png")

    cards, sprites = await asyncio.gather(
        asyncio.gather(*(card_for(d) for d in details)),
        pokeapi.get_images([m.sprite for m in team.members]),
    )
    content = await run_in_threadpool(team_to_pdf, team, analysis, list(cards), sprites)

    slug = _ascii_slug(team.name, fallback=str(team.id))
    stamp = datetime.now(UTC).strftime("%Y%m%d")
    return Response(
        content=content,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="equipo-{slug}-{stamp}.pdf"',
            "Cache-Control": "no-store",
        },
    )
