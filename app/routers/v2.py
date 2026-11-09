"""API v2: ejemplo de evolución de la API con cambios incompatibles con la v1.

¿Por qué una versión nueva y no cambiar la v1? Porque estos cambios **romperían** a los
clientes actuales:

1. **Forma de la respuesta:** la v1 devuelve ``{count, limit, offset, results}`` y la v2
   ``{items, limit, next_cursor}``.
2. **Paginación por cursor** en lugar de ``limit`` y ``offset``. Con ``offset``, si se
   añade o borra una entrada mientras se pagina, se repiten o se saltan elementos, y
   ``OFFSET 10000`` obliga a la base de datos a recorrer 10 000 filas. El cursor recuerda
   el último ID visto y la consulta usa el índice (``WHERE id > :cursor``).
3. **Datos de evolución** en cada entrada, que requieren consultar PokeAPI.

La v1 sigue funcionando sin cambios, pero ``GET /api/v1/pokedex`` queda marcado como
obsoleto (ver ``app/routers/pokedex.py``).
"""

import asyncio
import base64
import binascii
import json
import logging
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request, Response, status
from sqlmodel import select

from app import rate_limit as rl
from app.dependencies import CurrentUser, PokeAPIDep, SessionDep
from app.models import (
    EntryEvolution,
    EvolutionChain,
    EvolutionTarget,
    PokedexCursorPage,
    PokedexEntry,
    PokedexEntryRead,
    PokedexEntryV2,
)
from app.rate_limit import limiter
from app.routers.pokemon import IdOrName
from app.services.pokeapi_service import PokeAPIError, PokeAPIService

logger = logging.getLogger("pokedex_api.v2")

router = APIRouter(tags=["v2"])

CURSOR_VERSION = 1


# ═════════════════════════════════════════════════════════════
#  Cursor opaco
# ═════════════════════════════════════════════════════════════
def encode_cursor(last_id: int) -> str:
    """El cursor es opaco para el cliente: base64url de un pequeño JSON versionado.

    Así se puede cambiar su contenido en el futuro sin cambiar el contrato. No hace falta
    firmarlo: manipularlo solo permitiría paginar de otra forma la propia Pokédex, porque
    la consulta siempre filtra por el usuario del token.
    """
    raw = json.dumps({"v": CURSOR_VERSION, "after": last_id}, separators=(",", ":"))
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def decode_cursor(cursor: str) -> int:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        data = json.loads(base64.urlsafe_b64decode(padded.encode()))
        after = data["after"]
        if data.get("v") != CURSOR_VERSION or not isinstance(after, int) or after < 0:
            raise ValueError
    except (binascii.Error, ValueError, KeyError, TypeError, UnicodeDecodeError):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Cursor no válido. Usa el valor next_cursor de la respuesta anterior.",
        ) from None
    return after


# ═════════════════════════════════════════════════════════════
#  Evolución de cada entrada
# ═════════════════════════════════════════════════════════════
def entry_evolution(chain: EvolutionChain, pokemon_name: str) -> EntryEvolution | None:
    step = next((s for s in chain.steps if s.name == pokemon_name), None)
    if step is None:
        return None
    targets = [
        EvolutionTarget(id=s.id, name=s.name, condition=s.condition)
        for s in chain.steps
        if s.evolves_from == step.name
    ]
    return EntryEvolution(
        chain_id=chain.id,
        stage=step.stage,
        total_stages=chain.stages,
        evolves_from=step.evolves_from,
        evolves_to=targets,
        is_final_stage=not targets,
    )


async def _chain_for(pokeapi: PokeAPIService, pokemon_id: int) -> EvolutionChain | None:
    try:
        species = await pokeapi.get_species(pokemon_id)
        if species.evolution_chain_id is None:
            return None
        return await pokeapi.get_evolution_chain(species.evolution_chain_id)
    except PokeAPIError:
        return None


async def _evolutions(
    pokeapi: PokeAPIService, entries: list[PokedexEntry]
) -> dict[int, EntryEvolution | None]:
    """Evolución de cada entrada, pidiendo cada especie una sola vez y en paralelo.

    Si PokeAPI falla, esa entrada sale con ``evolution: null`` y el listado sigue
    funcionando: la Pokédex del usuario no depende de un servicio externo.
    """
    pokemon_ids = list(dict.fromkeys(e.pokemon_id for e in entries))
    chains = dict(
        zip(
            pokemon_ids,
            await asyncio.gather(*(_chain_for(pokeapi, pid) for pid in pokemon_ids)),
            strict=True,
        )
    )
    return {
        e.id: (entry_evolution(chain, e.pokemon_name) if (chain := chains[e.pokemon_id]) else None)
        for e in entries
    }


# ═════════════════════════════════════════════════════════════
#  Endpoints
# ═════════════════════════════════════════════════════════════
@router.get(
    "/pokedex",
    response_model=PokedexCursorPage,
    summary="Listar mi Pokédex (v2: con evoluciones y paginación por cursor)",
    responses={422: {"description": "Cursor o parámetros no válidos"}},
)
@limiter.limit(rl.EXTERNAL)
async def list_pokedex_v2(
    request: Request,
    response: Response,
    user: CurrentUser,
    session: SessionDep,
    pokeapi: PokeAPIDep,
    captured: Annotated[bool | None, Query()] = None,
    favorite: Annotated[bool | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    cursor: Annotated[
        str | None,
        Query(max_length=200, description="`next_cursor` de la página anterior."),
    ] = None,
) -> PokedexCursorPage:
    """Pokédex del usuario en orden de alta, con los **datos de evolución** de cada
    Pokémon (etapa, de quién evoluciona, a qué y cómo).

    Se pagina con `limit` y `cursor`: la primera petición va sin cursor y las siguientes
    usan el `next_cursor` de la respuesta anterior, con los mismos filtros.
    """
    query = select(PokedexEntry).where(PokedexEntry.owner_id == user.id)
    if captured is not None:
        query = query.where(PokedexEntry.is_captured == captured)
    if favorite is not None:
        query = query.where(PokedexEntry.favorite == favorite)
    if cursor is not None:
        query = query.where(PokedexEntry.id > decode_cursor(cursor))

    # Se pide un elemento de más para saber si hay página siguiente sin contar el total.
    rows = list(session.exec(query.order_by(PokedexEntry.id).limit(limit + 1)).all())
    page, has_more = rows[:limit], len(rows) > limit
    evolutions = await _evolutions(pokeapi, page)

    items = [
        PokedexEntryV2(
            **PokedexEntryRead.model_validate(e).model_dump(), evolution=evolutions[e.id]
        )
        for e in page
    ]
    return PokedexCursorPage(
        items=items,
        limit=limit,
        next_cursor=encode_cursor(page[-1].id) if has_more and page else None,
    )


@router.get(
    "/pokemon/{id_or_name}/evolution",
    response_model=EvolutionChain,
    summary="Cadena evolutiva de un Pokémon",
    responses={404: {"description": "El Pokémon no existe o no tiene cadena evolutiva"}},
)
@limiter.limit(rl.EXTERNAL)
async def get_evolution_chain(
    request: Request, response: Response, id_or_name: IdOrName, _: CurrentUser, pokeapi: PokeAPIDep
) -> EvolutionChain:
    """Cadena evolutiva completa, con las ramificaciones (Eevee tiene 8) y la condición de
    cada evolución en español: «nivel 16», «usar Piedra Trueno», «amistad alta, de día»…"""
    pokemon = await pokeapi.get_pokemon(id_or_name)
    species = await pokeapi.get_species(pokemon.species_name or pokemon.id)
    if species.evolution_chain_id is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail="Este Pokémon no tiene cadena evolutiva."
        )
    return await pokeapi.get_evolution_chain(species.evolution_chain_id)
