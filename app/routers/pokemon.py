"""Endpoints de Pokémon: proxy a PokeAPI con búsqueda, detalle y ficha descargable."""

import asyncio
from typing import Annotated, Literal

from fastapi import APIRouter, Path, Query, Request, Response
from starlette.concurrency import run_in_threadpool

from app import rate_limit as rl
from app.dependencies import CurrentUser, PokeAPIDep
from app.models import (
    KantoPokemon,
    PokemonDetail,
    PokemonFullDetail,
    PokemonSearchResult,
    PokemonSpecies,
)
from app.rate_limit import limiter
from app.services.card_renderer import card_filename, encode_card, render_card
from app.services.pokeapi_service import PokeAPIError, PokeAPIService

router = APIRouter(prefix="/pokemon", tags=["Pokémon"])

IdOrName = Annotated[
    str,
    Path(
        min_length=1,
        max_length=50,
        description="ID de Pokédex (p. ej. `25`) o nombre en inglés (p. ej. `pikachu`).",
        examples=["pikachu"],
    ),
]

ERRORS = {
    401: {"description": "Sin token o token no válido"},
    404: {"description": "El Pokémon no existe"},
    422: {"description": "Identificador o parámetros no válidos"},
    503: {"description": "PokeAPI no está disponible y no hay datos de respaldo"},
}


async def _species_for(pokeapi: PokeAPIService, pokemon: PokemonDetail) -> PokemonSpecies | None:
    """La especie completa la ficha, pero no es imprescindible: si falla, se omite."""
    try:
        return await pokeapi.get_species(pokemon.species_name or pokemon.id)
    except PokeAPIError:
        return None


@router.get(
    "/search",
    response_model=PokemonSearchResult,
    summary="Buscar Pokémon",
    responses={k: v for k, v in ERRORS.items() if k != 404},
)
@limiter.limit(rl.EXTERNAL)
async def search_pokemon(
    request: Request,
    response: Response,
    _: CurrentUser,
    pokeapi: PokeAPIDep,
    name: Annotated[
        str | None,
        Query(min_length=1, max_length=50, description="Texto que debe contener el nombre."),
    ] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> PokemonSearchResult:
    """Busca en PokeAPI los Pokémon cuyo nombre contiene `name` (sin distinguir mayúsculas).

    Devuelve una lista simplificada `{id, name, sprite, types}` paginada con `limit` y
    `offset`. Sin `name`, lista todos los Pokémon en orden de Pokédex.
    """
    return await pokeapi.search_pokemon(name, limit=limit, offset=offset)


@router.get(
    "/kanto",
    response_model=list[KantoPokemon],
    summary="Catálogo de los 151 de Kanto",
    responses={401: ERRORS[401]},
)
@limiter.limit(rl.DEFAULT)
def kanto_catalog(
    request: Request, response: Response, _: CurrentUser, pokeapi: PokeAPIDep
) -> list[KantoPokemon]:
    """Los 151 Pokémon originales con sus tipos, imágenes y estadística total, **en una sola
    petición**.

    Sale de la copia local de los datos de PokeAPI, sin llamar a PokeAPI: por eso tiene el
    límite general (100/minuto) y no el de los endpoints que consultan el servicio externo.
    Es lo que usa la web para pintar la Pokédex al instante. Para el resto de
    generaciones, `GET /pokemon/search`.
    """
    fallback = pokeapi.fallback
    species = fallback.species_by_id()
    # Los datos no cambian: el navegador puede guardarlos una hora (es privado: requiere token).
    response.headers["Cache-Control"] = "private, max-age=3600"
    return [
        KantoPokemon(
            id=p.id,
            name=p.name,
            sprite=p.sprites.front_default,
            artwork=p.sprites.official_artwork,
            types=p.types,
            total=sum(p.stats.model_dump().values()),
            genus=species.get(p.id, {}).get("genus"),
            is_legendary=bool(species.get(p.id, {}).get("is_legendary")),
            is_mythical=bool(species.get(p.id, {}).get("is_mythical")),
        )
        for p in fallback.all_pokemon()
    ]


@router.get(
    "/{id_or_name}",
    response_model=PokemonFullDetail,
    summary="Detalle de un Pokémon",
    responses=ERRORS,
)
@limiter.limit(rl.EXTERNAL)
async def get_pokemon(
    request: Request, response: Response, id_or_name: IdOrName, _: CurrentUser, pokeapi: PokeAPIDep
) -> PokemonFullDetail:
    """Detalle completo: estadísticas, habilidades, tipos, sprites y la información de su
    especie (descripción de la Pokédex y categoría en español, generación, legendario...)."""
    pokemon = await pokeapi.get_pokemon(id_or_name)
    species = await _species_for(pokeapi, pokemon)
    return PokemonFullDetail(**pokemon.model_dump(), species=species)


@router.get(
    "/{id_or_name}/card",
    summary="Descargar la ficha de un Pokémon",
    response_class=Response,
    responses={
        200: {
            "description": "Ficha como archivo descargable",
            "content": {"image/png": {}, "application/pdf": {}},
        },
        **ERRORS,
    },
)
@limiter.limit(rl.EXTERNAL)
async def get_pokemon_card(
    request: Request,
    id_or_name: IdOrName,
    _: CurrentUser,
    pokeapi: PokeAPIDep,
    format: Annotated[Literal["png", "pdf"], Query(description="Formato del archivo.")] = "png",
) -> Response:
    """Genera una ficha de estilo carta coleccionable (600 x 840 px) con la ilustración
    oficial, los tipos, las seis estadísticas, las habilidades en español y la descripción
    de la Pokédex. Se descarga como **PNG** (por defecto) o **PDF**."""
    pokemon = await pokeapi.get_pokemon(id_or_name)
    species, artwork, ability_names = await asyncio.gather(
        _species_for(pokeapi, pokemon),
        pokeapi.get_image(pokemon.sprites.official_artwork or pokemon.sprites.front_default),
        pokeapi.get_ability_names([a.name for a in pokemon.abilities]),
    )

    # Dibujar con Pillow consume CPU: se hace en un hilo aparte para no bloquear el
    # bucle de eventos que atiende al resto de peticiones.
    card = await run_in_threadpool(render_card, pokemon, species, artwork, ability_names)
    content = await run_in_threadpool(encode_card, card, format)

    return Response(
        content=content,
        media_type="application/pdf" if format == "pdf" else "image/png",
        headers={
            "Content-Disposition": f'attachment; filename="{card_filename(pokemon, format)}"',
            "Cache-Control": "private, max-age=3600",
        },
    )
