"""Cliente REST de la PokeAPI pública (https://pokeapi.co).

Es la única pieza de la aplicación que habla con PokeAPI. Al resto le entrega modelos
propios ya validados (``PokemonDetail``, ``PokemonSpecies``...) y excepciones de dominio,
nunca respuestas en bruto ni errores de ``httpx``.

Cómo se protege de los fallos de una API externa:

1. **Timeout** en cada petición: una PokeAPI lenta no bloquea nuestros workers.
2. **Límite de reintentos** con *backoff* exponencial y *jitter*, y solo ante errores
   transitorios (red, *timeout*, 429 y 5xx). Un 404 no se reintenta: no va a cambiar.
   Si PokeAPI envía ``Retry-After``, se respeta.
3. **Circuit breaker**: si PokeAPI falla varias veces seguidas se deja de llamar durante
   un tiempo y se responde al instante, en lugar de acumular peticiones colgadas.
4. **Caché con caducidad**, también de los 404 (durante menos tiempo), para no repetir
   consultas: los datos de un Pokémon prácticamente no cambian.
5. **Concurrencia limitada** con un semáforo, para no saturar un servicio gratuito.
6. **Datos de respaldo** con los 151 Pokémon originales, para seguir respondiendo si
   PokeAPI está caída (lo sugiere el apartado de preguntas frecuentes del enunciado).

Todas las llamadas externas se registran con su URL, código de estado y duración.
"""

import asyncio
import json
import logging
import random
import re
import time
from collections.abc import Awaitable, Callable
from functools import cached_property
from pathlib import Path
from typing import Any, Final

import httpx

from app.config import Settings, get_settings
from app.models import (
    NamedResource,
    PokemonAbility,
    PokemonDetail,
    PokemonPage,
    PokemonSearchResult,
    PokemonSpecies,
    PokemonSprites,
    PokemonStats,
    PokemonSummary,
)
from app.services.resilience import CircuitBreaker, Clock, TTLCache

logger = logging.getLogger("pokedex_api.pokeapi")

FALLBACK_DATA_PATH: Final = (
    Path(__file__).resolve().parent.parent / "data" / "pokemon_fallback.json"
)

MAX_POKEMON_ID: Final = 100_000
MAX_PAGE_SIZE: Final = 100
NOT_FOUND_TTL_SECONDS: Final = 300
INDEX_TTL_SECONDS: Final = 24 * 3600
MAX_RETRY_AFTER_SECONDS: Final = 10.0
MAX_IMAGE_BYTES: Final = 2 * 1024 * 1024

# Las imágenes solo se descargan de los repositorios de sprites de PokeAPI. Sin esta
# lista, una URL manipulada podría hacer que el servidor pidiera recursos internos (SSRF).
ALLOWED_IMAGE_HOSTS: Final = frozenset({"raw.githubusercontent.com"})
ALLOWED_IMAGE_PATH_PREFIX: Final = "/PokeAPI/sprites/"

_NAME_PATTERN: Final = re.compile(r"^[a-z0-9][a-z0-9-]{0,49}$")
_TYPE_PATTERN: Final = re.compile(r"^[a-z][a-z-]{1,19}$")
_ID_IN_URL: Final = re.compile(r"/(\d+)/?$")
_RETRYABLE_STATUS: Final = frozenset({429, 500, 502, 503, 504})
_PREFERRED_LANGUAGES: Final = ("es", "en")

_STAT_NAMES: Final = {
    "hp": "hp",
    "attack": "attack",
    "defense": "defense",
    "special-attack": "special_attack",
    "special-defense": "special_defense",
    "speed": "speed",
}


# ═════════════════════════════════════════════════════════════
#  Excepciones de dominio
# ═════════════════════════════════════════════════════════════
class PokeAPIError(Exception):
    """Error genérico al consultar PokeAPI."""


class InvalidIdentifierError(PokeAPIError, ValueError):
    """El identificador no tiene un formato válido; ni siquiera se consulta PokeAPI."""


class PokemonNotFoundError(PokeAPIError):
    """PokeAPI responde 404: el recurso no existe."""

    def __init__(self, resource: str, identifier: str) -> None:
        super().__init__(f"{resource} '{identifier}' no encontrado")
        self.resource = resource
        self.identifier = identifier


class PokeAPIUnavailableError(PokeAPIError):
    """PokeAPI no responde o falla de forma persistente y no hay datos de respaldo."""


class _NotFoundMarker:
    """Valor que se guarda en caché para recordar que algo no existe."""


_NOT_FOUND: Final = _NotFoundMarker()


# ═════════════════════════════════════════════════════════════
#  Validación de parámetros
# ═════════════════════════════════════════════════════════════
def normalize_identifier(identifier: str | int) -> str:
    """Valida un ID o nombre de Pokémon y lo devuelve normalizado.

    Rechaza cualquier cosa que no sea un número positivo o un nombre en minúsculas con
    guiones. Además de evitar llamadas inútiles, impide manipular la URL externa (por
    ejemplo, con ``../`` o ``?``) a través del parámetro.
    """
    if isinstance(identifier, bool):
        raise InvalidIdentifierError("Identificador de Pokémon no válido.")
    if isinstance(identifier, int) or str(identifier).strip().isdigit():
        number = int(identifier)
        if not 1 <= number <= MAX_POKEMON_ID:
            raise InvalidIdentifierError(f"El ID debe estar entre 1 y {MAX_POKEMON_ID}.")
        return str(number)

    name = str(identifier).strip().lower()
    if not _NAME_PATTERN.fullmatch(name):
        raise InvalidIdentifierError(
            "El nombre solo puede tener letras, números y guiones (máximo 50 caracteres)."
        )
    return name


def normalize_type(type_name: str) -> str:
    value = type_name.strip().lower()
    if not _TYPE_PATTERN.fullmatch(value):
        raise InvalidIdentifierError("Nombre de tipo no válido.")
    return value


def _validate_page(limit: int, offset: int) -> None:
    if not 1 <= limit <= MAX_PAGE_SIZE:
        raise InvalidIdentifierError(f"limit debe estar entre 1 y {MAX_PAGE_SIZE}.")
    if offset < 0:
        raise InvalidIdentifierError("offset no puede ser negativo.")


def _id_from_url(url: str) -> int:
    match = _ID_IN_URL.search(url)
    if match is None:
        raise PokeAPIError(f"URL de PokeAPI sin ID: {url}")
    return int(match.group(1))


# ═════════════════════════════════════════════════════════════
#  Transformación de respuestas
# ═════════════════════════════════════════════════════════════
def _clean_text(text: str) -> str:
    """Las entradas de la Pokédex traen saltos de línea y caracteres de control."""
    return re.sub(r"\s+", " ", text.replace("\xad", "")).strip()


def _pick_language(entries: list[dict[str, Any]], field: str) -> str | None:
    """Elige la entrada más reciente en español y, si no hay, en inglés."""
    for language in _PREFERRED_LANGUAGES:
        matches = [e[field] for e in entries if e.get("language", {}).get("name") == language]
        if matches:
            return _clean_text(matches[-1])
    return None


def to_pokemon_detail(data: dict[str, Any]) -> PokemonDetail:
    stats = {
        _STAT_NAMES[s["stat"]["name"]]: s["base_stat"]
        for s in data.get("stats", [])
        if s["stat"]["name"] in _STAT_NAMES
    }
    sprites = data.get("sprites") or {}
    artwork = ((sprites.get("other") or {}).get("official-artwork") or {}).get("front_default")
    return PokemonDetail(
        id=data["id"],
        name=data["name"],
        species_name=(data.get("species") or {}).get("name"),
        height=data.get("height") or 0,
        weight=data.get("weight") or 0,
        base_experience=data.get("base_experience"),
        types=[t["type"]["name"] for t in sorted(data.get("types", []), key=lambda t: t["slot"])],
        abilities=[
            PokemonAbility(name=a["ability"]["name"], is_hidden=a.get("is_hidden", False))
            for a in sorted(data.get("abilities", []), key=lambda a: a["slot"])
        ],
        stats=PokemonStats(**{name: stats.get(name, 0) for name in _STAT_NAMES.values()}),
        sprites=PokemonSprites(
            front_default=sprites.get("front_default"),
            front_shiny=sprites.get("front_shiny"),
            official_artwork=artwork,
        ),
    )


def to_pokemon_species(data: dict[str, Any]) -> PokemonSpecies:
    chain_url = (data.get("evolution_chain") or {}).get("url")
    return PokemonSpecies(
        id=data["id"],
        name=data["name"],
        display_name=_pick_language(data.get("names", []), "name") or data["name"].title(),
        genus=_pick_language(data.get("genera", []), "genus"),
        description=_pick_language(data.get("flavor_text_entries", []), "flavor_text"),
        generation=(data.get("generation") or {}).get("name"),
        is_legendary=data.get("is_legendary", False),
        is_mythical=data.get("is_mythical", False),
        evolution_chain_id=_id_from_url(chain_url) if chain_url else None,
    )


# ═════════════════════════════════════════════════════════════
#  Datos de respaldo
# ═════════════════════════════════════════════════════════════
class FallbackData:
    """Copia local de los 151 Pokémon originales, generada con
    ``scripts/generate_fallback_data.py``. Solo se usa si PokeAPI no está disponible."""

    def __init__(self, path: Path = FALLBACK_DATA_PATH) -> None:
        self._path = path

    @cached_property
    def _raw(self) -> dict[str, Any]:
        try:
            return json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            logger.warning("No se han podido cargar los datos de respaldo de %s", self._path)
            return {"pokemon": {}, "species": {}}

    @cached_property
    def _by_name(self) -> dict[str, str]:
        return {p["name"]: pid for pid, p in self._raw["pokemon"].items()}

    def _key(self, identifier: str) -> str | None:
        return identifier if identifier in self._raw["pokemon"] else self._by_name.get(identifier)

    def pokemon(self, identifier: str) -> PokemonDetail | None:
        key = self._key(identifier)
        return PokemonDetail.model_validate(self._raw["pokemon"][key]) if key else None

    def species(self, identifier: str) -> PokemonSpecies | None:
        key = self._key(identifier)
        data = self._raw["species"].get(key) if key else None
        return PokemonSpecies.model_validate(data) if data else None

    def index(self) -> list[NamedResource]:
        return [
            NamedResource(id=int(pid), name=p["name"])
            for pid, p in sorted(self._raw["pokemon"].items(), key=lambda kv: int(kv[0]))
        ]

    def by_type(self, type_name: str) -> list[NamedResource]:
        return [
            NamedResource(id=int(pid), name=p["name"])
            for pid, p in sorted(self._raw["pokemon"].items(), key=lambda kv: int(kv[0]))
            if type_name in p["types"]
        ]


# ═════════════════════════════════════════════════════════════
#  Servicio
# ═════════════════════════════════════════════════════════════
Sleeper = Callable[[float], Awaitable[None]]


class PokeAPIService:
    """Cliente asíncrono de PokeAPI. Se crea una vez al arrancar la aplicación."""

    def __init__(
        self,
        client: httpx.AsyncClient | None = None,
        settings: Settings | None = None,
        *,
        clock: Clock = time.monotonic,
        sleep: Sleeper = asyncio.sleep,
        fallback: FallbackData | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._client = client or httpx.AsyncClient(
            base_url=self._settings.pokeapi_base_url,
            timeout=self._settings.pokeapi_timeout_seconds,
            headers={"Accept": "application/json", "User-Agent": "pokedex-api"},
            follow_redirects=True,
        )
        self._sleep = sleep
        self._semaphore = asyncio.Semaphore(self._settings.pokeapi_max_concurrency)
        self.cache: TTLCache[Any] = TTLCache(self._settings.pokeapi_cache_max_entries, clock)
        self.breaker = CircuitBreaker(
            self._settings.pokeapi_breaker_threshold,
            self._settings.pokeapi_breaker_cooldown_seconds,
            clock,
        )
        self.fallback = fallback or FallbackData()

    async def close(self) -> None:
        await self._client.aclose()

    # ── Petición base: caché, circuit breaker y reintentos ───
    async def _get_json(
        self,
        path: str,
        resource: str,
        identifier: str,
        params: dict[str, Any] | None = None,
        ttl: float | None = None,
    ) -> dict[str, Any]:
        cache_key = path + (
            "?" + "&".join(f"{k}={v}" for k, v in sorted(params.items())) if params else ""
        )
        cached = self.cache.get(cache_key)
        if cached is _NOT_FOUND:
            raise PokemonNotFoundError(resource, identifier)
        if cached is not None:
            logger.debug("PokeAPI caché: %s", cache_key)
            return cached

        if not self.breaker.allow_request():
            logger.warning("PokeAPI: circuito abierto, no se llama a %s", cache_key)
            raise PokeAPIUnavailableError("PokeAPI no está disponible temporalmente.")

        max_retries = self._settings.pokeapi_max_retries
        async with self._semaphore:
            for attempt in range(max_retries + 1):
                retry_after: float | None = None
                started = time.perf_counter()
                try:
                    response = await self._client.get(path, params=params)
                except httpx.TimeoutException:
                    logger.warning(
                        "PokeAPI GET %s -> timeout (intento %d/%d)",
                        cache_key,
                        attempt + 1,
                        max_retries + 1,
                    )
                except httpx.TransportError as exc:
                    logger.warning(
                        "PokeAPI GET %s -> error de red %s (intento %d/%d)",
                        cache_key,
                        exc.__class__.__name__,
                        attempt + 1,
                        max_retries + 1,
                    )
                else:
                    elapsed_ms = (time.perf_counter() - started) * 1000
                    logger.info(
                        "PokeAPI GET %s -> %d (%.0f ms, intento %d)",
                        response.url,
                        response.status_code,
                        elapsed_ms,
                        attempt + 1,
                    )
                    if response.status_code == 200:
                        try:
                            data = response.json()
                        except ValueError:
                            logger.warning("PokeAPI devolvió un JSON no válido para %s", cache_key)
                        else:
                            self.breaker.record_success()
                            self.cache.set(
                                cache_key,
                                data,
                                ttl
                                if ttl is not None
                                else self._settings.pokeapi_cache_ttl_seconds,
                            )
                            return data
                    elif response.status_code == 404:
                        self.breaker.record_success()  # PokeAPI funciona: el recurso no existe
                        self.cache.set(cache_key, _NOT_FOUND, NOT_FOUND_TTL_SECONDS)
                        raise PokemonNotFoundError(resource, identifier)
                    elif response.status_code in _RETRYABLE_STATUS:
                        retry_after = _parse_retry_after(response.headers.get("Retry-After"))
                    else:
                        self.breaker.record_success()
                        raise PokeAPIError(
                            f"Respuesta inesperada de PokeAPI: {response.status_code}"
                        )

                if attempt < max_retries:
                    await self._sleep(self._backoff(attempt, retry_after))

        self.breaker.record_failure()
        logger.error("PokeAPI GET %s: agotados %d intentos", cache_key, max_retries + 1)
        raise PokeAPIUnavailableError("PokeAPI no está disponible en este momento.")

    def _backoff(self, attempt: int, retry_after: float | None) -> float:
        """Backoff exponencial con *full jitter* (AWS Architecture Blog).

        El azar evita que muchos clientes reintenten exactamente a la vez.
        """
        if retry_after is not None:
            return retry_after
        return random.uniform(0, self._settings.pokeapi_backoff_seconds * 2**attempt)  # noqa: S311

    # ── API pública ──────────────────────────────────────────
    async def get_pokemon(self, identifier: str | int) -> PokemonDetail:
        """Datos completos de un Pokémon por ID o nombre."""
        key = normalize_identifier(identifier)
        try:
            data = await self._get_json(f"/pokemon/{key}", "Pokémon", key)
        except PokeAPIUnavailableError:
            fallback = self.fallback.pokemon(key)
            if fallback is None:
                raise
            logger.warning("PokeAPI no disponible: se usan datos de respaldo para %s", key)
            return fallback
        return to_pokemon_detail(data)

    async def get_species(self, identifier: str | int) -> PokemonSpecies:
        """Información de la especie: descripción, categoría, generación, legendario..."""
        key = normalize_identifier(identifier)
        try:
            data = await self._get_json(f"/pokemon-species/{key}", "Especie", key)
        except PokeAPIUnavailableError:
            fallback = self.fallback.species(key)
            if fallback is None:
                raise
            logger.warning("PokeAPI no disponible: especie de respaldo para %s", key)
            return fallback
        return to_pokemon_species(data)

    async def list_pokemon(self, limit: int = 20, offset: int = 0) -> PokemonPage:
        """Lista paginada de Pokémon (solo ID y nombre)."""
        _validate_page(limit, offset)
        try:
            data = await self._get_json(
                "/pokemon", "Página", f"{offset}", params={"limit": limit, "offset": offset}
            )
        except PokeAPIUnavailableError:
            index = self.fallback.index()
            if not index:
                raise
            logger.warning("PokeAPI no disponible: listado de respaldo")
            return PokemonPage(
                count=len(index), limit=limit, offset=offset, results=index[offset : offset + limit]
            )
        return PokemonPage(
            count=data["count"],
            limit=limit,
            offset=offset,
            results=[
                NamedResource(id=_id_from_url(item["url"]), name=item["name"])
                for item in data["results"]
            ],
        )

    async def get_pokemon_by_type(self, type_name: str) -> list[NamedResource]:
        """Todos los Pokémon de un tipo (``water``, ``fire``...)."""
        key = normalize_type(type_name)
        try:
            data = await self._get_json(f"/type/{key}", "Tipo", key)
        except PokeAPIUnavailableError:
            fallback = self.fallback.by_type(key)
            if not fallback:
                raise
            logger.warning("PokeAPI no disponible: tipo %s desde datos de respaldo", key)
            return fallback
        return [
            NamedResource(id=_id_from_url(p["pokemon"]["url"]), name=p["pokemon"]["name"])
            for p in data.get("pokemon", [])
        ]

    async def get_species_count(self) -> int | None:
        """Número de especies de la Pokédex Nacional. Cambia muy pocas veces (con cada
        juego nuevo), así que se cachea un día. ``None`` si PokeAPI no responde."""
        try:
            data = await self._get_json(
                "/pokemon-species", "Especies", "total", params={"limit": 1}, ttl=INDEX_TTL_SECONDS
            )
        except PokeAPIError:
            return None
        count = data.get("count")
        return count if isinstance(count, int) and count > 0 else None

    async def get_ability_names(self, names: list[str]) -> dict[str, str]:
        """Traduce nombres de habilidad (``static``) a su nombre en español.

        Es un dato decorativo: si una consulta falla, esa habilidad se queda sin traducir.
        """

        async def translate(name: str) -> tuple[str, str | None]:
            try:
                key = normalize_identifier(name)
                data = await self._get_json(f"/ability/{key}", "Habilidad", key)
            except PokeAPIError:
                return name, None
            return name, _pick_language(data.get("names", []), "name")

        results = await asyncio.gather(*(translate(n) for n in names))
        return {name: label for name, label in results if label}

    async def get_image(self, url: str | None) -> bytes | None:
        """Descarga un sprite de PokeAPI. Devuelve ``None`` si no se puede obtener.

        Solo acepta HTTPS hacia los repositorios de sprites de PokeAPI y como mucho 2 MB.
        Un fallo aquí no es grave: la ficha se dibuja igualmente sin imagen.
        """
        if not url:
            return None
        parsed = httpx.URL(url)
        if (
            parsed.scheme != "https"
            or parsed.host not in ALLOWED_IMAGE_HOSTS
            or not parsed.path.startswith(ALLOWED_IMAGE_PATH_PREFIX)
        ):
            logger.warning("Imagen rechazada por no estar en la lista permitida: %s", url)
            return None

        cached = self.cache.get(f"img:{url}")
        if cached is not None:
            return cached

        started = time.perf_counter()
        try:
            async with self._semaphore:
                response = await self._client.get(url)
        except httpx.HTTPError as exc:
            logger.warning("Imagen GET %s -> %s", url, exc.__class__.__name__)
            return None

        logger.info(
            "Imagen GET %s -> %d (%.0f ms)",
            url,
            response.status_code,
            (time.perf_counter() - started) * 1000,
        )
        content_type = response.headers.get("content-type", "")
        if (
            response.status_code != 200
            or not content_type.startswith("image/")
            or len(response.content) > MAX_IMAGE_BYTES
        ):
            return None
        self.cache.set(f"img:{url}", response.content, self._settings.pokeapi_cache_ttl_seconds)
        return response.content

    async def get_images(self, urls: list[str | None]) -> dict[str, bytes]:
        """Descarga varias imágenes en paralelo (sin repetir URLs) y devuelve las obtenidas."""
        unique = list(dict.fromkeys(u for u in urls if u))
        images = await asyncio.gather(*(self.get_image(u) for u in unique))
        return {url: image for url, image in zip(unique, images, strict=True) if image}

    async def _name_index(self) -> list[NamedResource]:
        """Índice completo de nombres. PokeAPI no tiene búsqueda por texto, así que se
        descarga la lista de nombres una vez al día y se filtra en local."""
        try:
            data = await self._get_json(
                "/pokemon",
                "Índice",
                "todos",
                params={"limit": MAX_POKEMON_ID},
                ttl=INDEX_TTL_SECONDS,
            )
        except PokeAPIUnavailableError:
            index = self.fallback.index()
            if not index:
                raise
            return index
        return [
            NamedResource(id=_id_from_url(item["url"]), name=item["name"])
            for item in data["results"]
        ]

    async def search_pokemon(
        self, name: str | None = None, limit: int = 20, offset: int = 0
    ) -> PokemonSearchResult:
        """Busca Pokémon cuyo nombre contiene ``name`` y devuelve ``{id, name, sprite, types}``.

        Los detalles de cada resultado se piden en paralelo, con la concurrencia limitada
        por el semáforo y aprovechando la caché.
        """
        _validate_page(limit, offset)
        query = name.strip().lower() if name else ""
        if query and not re.fullmatch(r"[a-z0-9-]{1,50}", query):
            raise InvalidIdentifierError("La búsqueda solo admite letras, números y guiones.")

        index = await self._name_index()
        matches = [p for p in index if query in p.name] if query else index
        page = matches[offset : offset + limit]

        async def summary(item: NamedResource) -> PokemonSummary | None:
            try:
                return (await self.get_pokemon(item.id)).to_summary()
            except PokemonNotFoundError:
                return None

        summaries = await asyncio.gather(*(summary(item) for item in page))
        return PokemonSearchResult(
            count=len(matches),
            limit=limit,
            offset=offset,
            results=[s for s in summaries if s is not None],
        )


def _parse_retry_after(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        return min(max(float(value), 0.0), MAX_RETRY_AFTER_SECONDS)
    except ValueError:
        return None
