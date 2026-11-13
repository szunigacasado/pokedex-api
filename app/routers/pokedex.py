"""Pokédex personal: cada usuario gestiona solo sus propias entradas.

Regla de oro de autorización: todas las consultas filtran por ``owner_id`` del usuario
autenticado. Si alguien pide una entrada que existe pero es de otro usuario, se responde
**404** y no 403, para no revelar qué IDs existen (enumeración de recursos, OWASP API1).
"""

import logging
from datetime import UTC, datetime
from email.utils import format_datetime
from enum import StrEnum
from typing import Annotated, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, HTTPException, Path, Query, Request, Response, status
from sqlalchemy import func, nulls_last
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select
from starlette.concurrency import run_in_threadpool

from app import rate_limit as rl
from app.config import get_settings
from app.dependencies import CurrentUser, PokeAPIDep, SessionDep
from app.models import (
    MAX_DB_ID,
    PokedexEntry,
    PokedexEntryCreate,
    PokedexEntryRead,
    PokedexEntryUpdate,
    PokedexPage,
    PokedexStats,
    User,
    utc_now,
)
from app.rate_limit import limiter
from app.services.exporters import pokedex_to_csv, pokedex_to_pdf
from app.services.stats import summarize

logger = logging.getLogger("pokedex_api.pokedex")

router = APIRouter(prefix="/pokedex", tags=["Pokédex"])

NOT_FOUND = "Entrada de Pokédex no encontrada."
EntryId = Annotated[int, Path(ge=1, le=MAX_DB_ID, description="ID de la entrada en tu Pokédex.")]


class SortField(StrEnum):
    pokemon_id = "pokemon_id"
    capture_date = "capture_date"
    pokemon_name = "pokemon_name"
    created_at = "created_at"


class SortOrder(StrEnum):
    asc = "asc"
    desc = "desc"


# ═════════════════════════════════════════════════════════════
#  Utilidades
# ═════════════════════════════════════════════════════════════
def _owned_entry(session: Session, user: User, entry_id: int) -> PokedexEntry:
    entry = session.exec(
        select(PokedexEntry).where(PokedexEntry.id == entry_id, PokedexEntry.owner_id == user.id)
    ).first()
    if entry is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=NOT_FOUND)
    return entry


def _filtered(user: User, captured: bool | None, favorite: bool | None):
    query = select(PokedexEntry).where(PokedexEntry.owner_id == user.id)
    if captured is not None:
        query = query.where(PokedexEntry.is_captured == captured)
    if favorite is not None:
        query = query.where(PokedexEntry.favorite == favorite)
    return query


CapturedFilter = Annotated[
    bool | None, Query(description="Solo capturados (`true`) o no (`false`).")
]
FavoriteFilter = Annotated[
    bool | None, Query(description="Solo favoritos (`true`) o no (`false`).")
]


# ═════════════════════════════════════════════════════════════
#  Crear y listar
# ═════════════════════════════════════════════════════════════
@router.post(
    "",
    response_model=PokedexEntryRead,
    status_code=status.HTTP_201_CREATED,
    summary="Añadir un Pokémon a mi Pokédex",
    responses={
        404: {"description": "El Pokémon no existe en PokeAPI"},
        409: {"description": "Ese Pokémon ya está en tu Pokédex"},
        503: {"description": "PokeAPI no está disponible"},
    },
)
@limiter.limit(rl.EXTERNAL)
async def add_entry(
    request: Request,
    response: Response,
    payload: PokedexEntryCreate,
    user: CurrentUser,
    session: SessionDep,
    pokeapi: PokeAPIDep,
) -> PokedexEntry:
    """Añade un Pokémon a la Pokédex del usuario autenticado.

    Antes de guardarlo se comprueba en PokeAPI que el `pokemon_id` existe y se copian su
    nombre, sprite y tipos. Así la Pokédex y sus estadísticas funcionan aunque PokeAPI se
    caiga después. Si se marca como capturado sin `capture_date`, se usa la fecha actual.
    """
    duplicate = session.exec(
        select(PokedexEntry.id).where(
            PokedexEntry.owner_id == user.id, PokedexEntry.pokemon_id == payload.pokemon_id
        )
    ).first()
    if duplicate is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Ese Pokémon ya está en tu Pokédex.")

    pokemon = await pokeapi.get_pokemon(payload.pokemon_id)

    entry = PokedexEntry(
        owner_id=user.id,
        pokemon_id=pokemon.id,
        pokemon_name=pokemon.name,
        pokemon_sprite=pokemon.sprites.front_default or pokemon.sprites.official_artwork,
        pokemon_types=pokemon.types,
        nickname=payload.nickname,
        notes=payload.notes,
        favorite=payload.favorite,
        is_captured=payload.is_captured,
        capture_date=payload.capture_date or (utc_now() if payload.is_captured else None),
    )
    session.add(entry)
    try:
        session.commit()
    except IntegrityError:
        # Dos peticiones simultáneas pueden pasar la comprobación anterior a la vez; la
        # restricción UNIQUE de la base de datos es la que garantiza que no haya duplicados.
        session.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT, detail="Ese Pokémon ya está en tu Pokédex."
        ) from None
    session.refresh(entry)
    logger.info("Pokédex: user_id=%s añade pokemon_id=%s", user.id, entry.pokemon_id)
    return entry


SUCCESSOR_LINK = '</api/v2/pokedex>; rel="successor-version"'


def _deprecation_headers(response: Response) -> None:
    """Cabeceras estándar de deprecación:

    - ``Deprecation`` (RFC 9745): desde cuándo está obsoleto, como ``@<timestamp>``.
    - ``Sunset`` (RFC 8594): fecha HTTP en la que dejará de funcionar.
    - ``Link`` con ``rel="successor-version"``: a dónde migrar.
    """
    settings = get_settings()
    response.headers["Deprecation"] = f"@{int(settings.v1_pokedex_deprecated_at.timestamp())}"
    response.headers["Sunset"] = format_datetime(settings.v1_pokedex_sunset, usegmt=True)
    response.headers["Link"] = SUCCESSOR_LINK


@router.get(
    "",
    response_model=PokedexPage,
    summary="Listar mi Pokédex (obsoleto: usa /api/v2/pokedex)",
    deprecated=True,
    responses={410: {"description": "Retirado: la fecha de Sunset ya ha pasado"}},
)
@limiter.limit(rl.DEFAULT)
def list_entries(
    request: Request,
    response: Response,
    user: CurrentUser,
    session: SessionDep,
    captured: CapturedFilter = None,
    favorite: FavoriteFilter = None,
    sort: Annotated[SortField, Query(description="Campo de ordenación.")] = SortField.pokemon_id,
    order: Annotated[SortOrder, Query(description="Sentido de la ordenación.")] = SortOrder.asc,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0, le=MAX_DB_ID)] = 0,
) -> PokedexPage:
    """Lista la Pokédex del usuario con filtros, ordenación y paginación.

    `count` es el total de entradas que cumplen los filtros, para que el cliente pueda
    construir la paginación. Al ordenar por `capture_date`, los no capturados van al final.

    **Obsoleto:** sigue funcionando igual, pero cada respuesta lleva las cabeceras
    `Deprecation`, `Sunset` y `Link` hacia `/api/v2/pokedex`. Después de la fecha de
    Sunset responde `410 Gone`.
    """
    if utc_now() >= get_settings().v1_pokedex_sunset:
        raise HTTPException(
            status.HTTP_410_GONE,
            detail="Esta versión del endpoint se ha retirado. Usa /api/v2/pokedex.",
            headers={"Link": SUCCESSOR_LINK},
        )
    _deprecation_headers(response)
    # Saber quién sigue usando la v1 ayuda a decidir cuándo retirarla.
    logger.info("Uso de endpoint obsoleto GET /api/v1/pokedex (user_id=%s)", user.id)

    query = _filtered(user, captured, favorite)
    count = session.exec(select(func.count()).select_from(query.subquery())).one()

    column = getattr(PokedexEntry, sort.value)
    direction = column.desc() if order is SortOrder.desc else column.asc()
    query = query.order_by(nulls_last(direction), PokedexEntry.id).offset(offset).limit(limit)

    results = [PokedexEntryRead.model_validate(e) for e in session.exec(query).all()]
    return PokedexPage(count=count, limit=limit, offset=offset, results=results)


# ═════════════════════════════════════════════════════════════
#  Exportar (antes que /{entry_id} para que "export" no se lea como un ID)
# ═════════════════════════════════════════════════════════════
@router.get(
    "/export",
    summary="Exportar mi Pokédex",
    response_class=Response,
    responses={200: {"content": {"text/csv": {}, "application/pdf": {}}}},
)
@limiter.limit(rl.EXPORT)
async def export_entries(
    request: Request,
    user: CurrentUser,
    session: SessionDep,
    pokeapi: PokeAPIDep,
    format: Annotated[Literal["csv", "pdf"], Query(description="Formato del archivo.")] = "csv",
    captured: CapturedFilter = None,
    favorite: FavoriteFilter = None,
) -> Response:
    """Descarga la Pokédex completa (o filtrada) como **CSV** o **PDF**.

    - El CSV usa UTF-8 con BOM para que Excel muestre bien los acentos, y protege contra
      la inyección de fórmulas en hojas de cálculo.
    - El PDF incluye un resumen y una tabla paginada con el sprite de cada Pokémon.
    """
    query = _filtered(user, captured, favorite).order_by(PokedexEntry.pokemon_id)
    entries = list(session.exec(query).all())
    stamp = datetime.now(UTC).strftime("%Y%m%d")
    filename = f"pokedex-{user.username}-{stamp}.{format}"

    if format == "csv":
        content = pokedex_to_csv(entries)
        media_type = "text/csv; charset=utf-8"
    else:
        sprites = await pokeapi.get_images([e.pokemon_sprite for e in entries])
        filters = {"captured": captured, "favorite": favorite}
        content = await run_in_threadpool(pokedex_to_pdf, entries, user.username, sprites, filters)
        media_type = "application/pdf"

    logger.info("Pokédex: user_id=%s exporta %d entradas en %s", user.id, len(entries), format)
    return Response(
        content=content,
        media_type=media_type,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
        },
    )


# ═════════════════════════════════════════════════════════════
#  Estadísticas (también antes que /{entry_id})
# ═════════════════════════════════════════════════════════════
def _parse_timezone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Zona horaria no válida. Usa un nombre IANA, por ejemplo Europe/Madrid.",
        ) from None


@router.get("/stats", response_model=PokedexStats, summary="Estadísticas de mi Pokédex")
@limiter.limit(rl.DEFAULT)
async def pokedex_stats(
    request: Request,
    response: Response,
    user: CurrentUser,
    session: SessionDep,
    pokeapi: PokeAPIDep,
    tz: Annotated[
        str,
        Query(
            max_length=64,
            description="Zona horaria IANA para decidir dónde empieza cada día de la racha.",
            examples=["Europe/Madrid"],
        ),
    ] = "UTC",
) -> PokedexStats:
    """Resumen de la Pokédex del usuario.

    - `completion_percentage`: porcentaje de los Pokémon de la Pokédex que están
      capturados, como en el ejemplo del enunciado (89 de 150 → 59,3).
    - `capture_streak_days`: días consecutivos con al menos una captura hasta hoy. Si hoy
      aún no hay capturas pero ayer sí, la racha sigue viva.
    - `national_dex_percentage`: capturados respecto al total de especies que existen
      según PokeAPI. Es el único dato que depende de PokeAPI; si no responde, es `null`.

    Los días se cuentan en la zona horaria `tz`: una captura a las 23:30 UTC es «mañana»
    en Madrid.
    """
    zone = _parse_timezone(tz)
    entries = list(session.exec(select(PokedexEntry).where(PokedexEntry.owner_id == user.id)).all())
    summary = summarize(entries, zone, utc_now())
    national = await pokeapi.get_species_count()

    return PokedexStats(
        total_pokemon=summary.total,
        captured=summary.captured,
        favorites=summary.favorites,
        completion_percentage=summary.completion_percentage,
        most_common_type=summary.most_common_type,
        capture_streak_days=summary.streaks.current,
        longest_streak_days=summary.streaks.longest,
        type_distribution=summary.type_distribution,
        first_capture_date=summary.first_capture,
        last_capture_date=summary.last_capture,
        national_dex_total=national,
        national_dex_percentage=round(100 * summary.captured / national, 1) if national else None,
        timezone=tz,
    )


# ═════════════════════════════════════════════════════════════
#  Consultar, actualizar y borrar una entrada
# ═════════════════════════════════════════════════════════════
@router.get(
    "/{entry_id}",
    response_model=PokedexEntryRead,
    summary="Consultar una entrada",
    responses={404: {"description": "No existe o no es tuya"}},
)
@limiter.limit(rl.DEFAULT)
def get_entry(
    request: Request, response: Response, entry_id: EntryId, user: CurrentUser, session: SessionDep
) -> PokedexEntry:
    return _owned_entry(session, user, entry_id)


@router.patch(
    "/{entry_id}",
    response_model=PokedexEntryRead,
    summary="Actualizar una entrada",
    responses={
        404: {"description": "No existe o no es tuya"},
        422: {"description": "Datos no válidos o incoherentes"},
    },
)
@limiter.limit(rl.DEFAULT)
def update_entry(
    request: Request,
    response: Response,
    entry_id: EntryId,
    payload: PokedexEntryUpdate,
    user: CurrentUser,
    session: SessionDep,
) -> PokedexEntry:
    """Actualización parcial: solo cambian los campos enviados (enviar `null` en
    `nickname` o `notes` los borra).

    Reglas de captura:
    - Marcar `is_captured: true` sin fecha usa la fecha actual (si no tenía ya una).
    - Marcar `is_captured: false` borra la fecha de captura.
    - No se admite una fecha de captura en un Pokémon no capturado, ni `capture_date: null`
      en uno capturado (para quitarla, se desmarca como capturado).
    """
    entry = _owned_entry(session, user, entry_id)
    changes = payload.model_dump(exclude_unset=True)

    if changes.get("is_captured") is False:
        if changes.get("capture_date") is not None:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="Solo un Pokémon capturado puede tener fecha de captura.",
            )
        changes["capture_date"] = None
    if (
        "capture_date" in changes
        and changes["capture_date"] is None
        and changes.get("is_captured", entry.is_captured)
    ):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Un Pokémon capturado necesita fecha de captura. Para quitarla, "
            "márcalo como no capturado (`is_captured: false`).",
        )
    for field in ("is_captured", "favorite"):
        if field in changes and changes[field] is None:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT, detail=f"`{field}` no puede ser null."
            )

    for field, value in changes.items():
        setattr(entry, field, value)

    if entry.capture_date is not None and not entry.is_captured:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Solo un Pokémon capturado puede tener fecha de captura.",
        )
    if entry.is_captured and entry.capture_date is None:
        entry.capture_date = utc_now()

    session.add(entry)
    session.commit()
    session.refresh(entry)
    return entry


@router.delete(
    "/{entry_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Eliminar una entrada",
    responses={404: {"description": "No existe o no es tuya"}},
)
@limiter.limit(rl.DEFAULT)
def delete_entry(
    request: Request, response: Response, entry_id: EntryId, user: CurrentUser, session: SessionDep
) -> None:
    """Elimina la entrada. Si formaba parte de algún equipo, sale también de él."""
    entry = _owned_entry(session, user, entry_id)
    session.delete(entry)
    session.commit()
    logger.info("Pokédex: user_id=%s elimina la entrada %s", user.id, entry_id)
