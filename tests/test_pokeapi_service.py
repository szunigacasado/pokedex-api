"""Tests del cliente de PokeAPI.

PokeAPI se simula con ``respx`` (ningún test usa la red) y el tiempo con un reloj falso,
de modo que los reintentos, la caché y el circuit breaker se prueban sin esperas reales.
"""

import logging
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
import respx

from app.config import get_settings
from app.services.pokeapi_service import (
    FallbackData,
    InvalidIdentifierError,
    PokeAPIError,
    PokeAPIService,
    PokeAPIUnavailableError,
    PokemonNotFoundError,
    _id_from_url,
    normalize_identifier,
)
from app.services.resilience import CircuitBreaker, TTLCache
from tests.fakes import BASE, pokemon_payload, species_payload


# ═════════════════════════════════════════════════════════════
#  Utilidades
# ═════════════════════════════════════════════════════════════
class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def sleeps() -> list[float]:
    return []


@pytest.fixture
def make_service(clock: FakeClock, sleeps: list[float]):
    created: list[PokeAPIService] = []

    def factory(**overrides) -> PokeAPIService:
        defaults = {
            "pokeapi_base_url": BASE,
            "pokeapi_max_retries": 2,
            "pokeapi_backoff_seconds": 0.5,
            "pokeapi_breaker_threshold": 3,
            "pokeapi_breaker_cooldown_seconds": 30,
            "pokeapi_cache_ttl_seconds": 60,
        }
        settings = get_settings().model_copy(update=defaults | overrides)

        async def fake_sleep(seconds: float) -> None:
            sleeps.append(seconds)

        service = PokeAPIService(
            httpx.AsyncClient(base_url=BASE), settings, clock=clock, sleep=fake_sleep
        )
        created.append(service)
        return service

    return factory


@pytest.fixture
def api() -> Iterator[respx.MockRouter]:
    with respx.mock(base_url=BASE, assert_all_called=False) as router:
        yield router


# ═════════════════════════════════════════════════════════════
#  Validación de parámetros
# ═════════════════════════════════════════════════════════════
async def test_identifier_is_normalized(api, make_service) -> None:
    route = api.get("/pokemon/pikachu").respond(json=pokemon_payload())

    await make_service().get_pokemon("  PiKaChU ")

    assert route.called


@pytest.mark.parametrize(
    "identifier",
    ["../admin", "pika chu", "", "0", "-1", 100_001, True, "a" * 51, "pikachu?x=1", "pikachu/"],
)
async def test_invalid_identifiers_never_reach_pokeapi(api, make_service, identifier) -> None:
    with pytest.raises(InvalidIdentifierError):
        await make_service().get_pokemon(identifier)

    assert not api.calls


def test_numeric_strings_are_ids() -> None:
    assert normalize_identifier(" 0025 ") == "25"


@pytest.mark.parametrize(("limit", "offset"), [(0, 0), (101, 0), (20, -1)])
async def test_invalid_pagination_is_rejected(api, make_service, limit, offset) -> None:
    with pytest.raises(InvalidIdentifierError):
        await make_service().list_pokemon(limit=limit, offset=offset)


# ═════════════════════════════════════════════════════════════
#  Reintentos
# ═════════════════════════════════════════════════════════════
async def test_transient_error_is_retried(api, make_service, sleeps) -> None:
    route = api.get("/pokemon/25").mock(
        side_effect=[httpx.Response(503), httpx.Response(200, json=pokemon_payload())]
    )

    pokemon = await make_service().get_pokemon(25)

    assert pokemon.name == "pikachu"
    assert route.call_count == 2
    assert len(sleeps) == 1
    assert 0 <= sleeps[0] <= 0.5


async def test_retries_are_limited_and_backoff_grows(api, make_service, sleeps) -> None:
    route = api.get("/pokemon/9999").mock(side_effect=httpx.ConnectTimeout("lento"))

    with pytest.raises(PokeAPIUnavailableError):
        await make_service(pokeapi_max_retries=3).get_pokemon(9999)

    assert route.call_count == 4  # 1 intento + 3 reintentos
    assert len(sleeps) == 3
    for attempt, delay in enumerate(sleeps):
        assert 0 <= delay <= 0.5 * 2**attempt


async def test_network_errors_are_retried(api, make_service) -> None:
    route = api.get("/pokemon/9999").mock(
        side_effect=[httpx.ConnectError("sin red"), httpx.Response(200, json=pokemon_payload(9999))]
    )

    assert (await make_service().get_pokemon(9999)).id == 9999
    assert route.call_count == 2


async def test_retry_after_header_is_respected_and_capped(api, make_service, sleeps) -> None:
    api.get("/pokemon/1").mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "2"}),
            httpx.Response(429, headers={"Retry-After": "3600"}),
            httpx.Response(200, json=pokemon_payload(1, "bulbasaur")),
        ]
    )

    await make_service().get_pokemon(1)

    assert sleeps == [2.0, 10.0]


async def test_unparseable_retry_after_falls_back_to_backoff(api, make_service, sleeps) -> None:
    api.get("/pokemon/1").mock(
        side_effect=[
            httpx.Response(503, headers={"Retry-After": "tomorrow"}),
            httpx.Response(200, json=pokemon_payload(1, "bulbasaur")),
        ]
    )

    await make_service().get_pokemon(1)

    assert len(sleeps) == 1
    assert 0 <= sleeps[0] <= 0.5


async def test_client_errors_are_not_retried(api, make_service) -> None:
    route = api.get("/pokemon/25").respond(400)

    with pytest.raises(PokeAPIError):
        await make_service().get_pokemon(25)

    assert route.call_count == 1


async def test_invalid_json_is_treated_as_a_failure(api, make_service) -> None:
    route = api.get("/pokemon/9999").respond(200, text="<html>mantenimiento</html>")

    with pytest.raises(PokeAPIUnavailableError):
        await make_service().get_pokemon(9999)

    assert route.call_count == 3


# ═════════════════════════════════════════════════════════════
#  Caché
# ═════════════════════════════════════════════════════════════
async def test_responses_are_cached_until_they_expire(api, make_service, clock) -> None:
    route = api.get("/pokemon/25").respond(json=pokemon_payload())
    service = make_service()

    await service.get_pokemon(25)
    await service.get_pokemon("25")
    assert route.call_count == 1

    clock.advance(61)
    await service.get_pokemon(25)
    assert route.call_count == 2


async def test_not_found_is_cached(api, make_service) -> None:
    route = api.get("/pokemon/missingno").respond(404)
    service = make_service()

    for _ in range(3):
        with pytest.raises(PokemonNotFoundError):
            await service.get_pokemon("missingno")

    assert route.call_count == 1


# ═════════════════════════════════════════════════════════════
#  Circuit breaker y datos de respaldo
# ═════════════════════════════════════════════════════════════
async def test_circuit_breaker_stops_calling_a_failing_api(api, make_service, clock) -> None:
    route = api.get(url__regex=r"/pokemon/\d+").mock(side_effect=httpx.ConnectTimeout("caída"))
    service = make_service(pokeapi_max_retries=0, pokeapi_breaker_threshold=2)

    for pid in (9001, 9002):
        with pytest.raises(PokeAPIUnavailableError):
            await service.get_pokemon(pid)
    assert service.breaker.state == "open"

    with pytest.raises(PokeAPIUnavailableError):
        await service.get_pokemon(9003)
    assert route.call_count == 2  # la tercera ni siquiera sale

    clock.advance(31)
    assert service.breaker.state == "half_open"
    route.mock(side_effect=None).respond(json=pokemon_payload(9004))
    assert (await service.get_pokemon(9004)).id == 9004
    assert service.breaker.state == "closed"


async def test_fallback_data_is_used_when_pokeapi_is_down(api, make_service, caplog) -> None:
    api.get("/pokemon/pikachu").mock(side_effect=httpx.ConnectTimeout("caída"))

    with caplog.at_level(logging.WARNING, logger="pokedex_api.pokeapi"):
        pokemon = await make_service().get_pokemon("pikachu")

    assert pokemon.id == 25
    assert pokemon.types == ["electric"]
    assert "datos de respaldo" in caplog.text


async def test_unknown_pokemon_without_fallback_is_unavailable(api, make_service) -> None:
    api.get("/pokemon/9999").respond(503)

    with pytest.raises(PokeAPIUnavailableError):
        await make_service(pokeapi_max_retries=0).get_pokemon(9999)


async def test_fallback_covers_species_lists_and_types(api, make_service) -> None:
    api.route().mock(side_effect=httpx.ConnectTimeout("caída"))
    service = make_service(pokeapi_max_retries=0, pokeapi_breaker_threshold=100)

    species = await service.get_species(150)
    page = await service.list_pokemon(limit=3, offset=0)
    electric = await service.get_pokemon_by_type("electric")
    search = await service.search_pokemon("pika")

    assert species.is_legendary and species.display_name == "Mewtwo"
    assert page.count == 151
    assert [p.name for p in page.results] == ["bulbasaur", "ivysaur", "venusaur"]
    assert "pikachu" in {p.name for p in electric}
    assert [p.name for p in search.results] == ["pikachu"]


async def test_missing_fallback_file_is_handled(api, make_service, tmp_path: Path, caplog) -> None:
    api.route().mock(side_effect=httpx.ConnectTimeout("caída"))
    service = make_service(pokeapi_max_retries=0, pokeapi_breaker_threshold=100)
    service.fallback = FallbackData(tmp_path / "no-existe.json")

    with pytest.raises(PokeAPIUnavailableError):
        await service.get_pokemon(25)
    for call in (
        service.get_species(25),
        service.list_pokemon(),
        service.get_pokemon_by_type("fire"),
        service.search_pokemon("pika"),
    ):
        with pytest.raises(PokeAPIUnavailableError):
            await call
    assert "datos de respaldo" in caplog.text


# ═════════════════════════════════════════════════════════════
#  Otros endpoints de PokeAPI
# ═════════════════════════════════════════════════════════════
async def test_list_pokemon(api, make_service) -> None:
    api.get("/pokemon", params={"limit": 2, "offset": 10}).respond(
        json={
            "count": 1351,
            "results": [
                {"name": "metapod", "url": f"{BASE}/pokemon/11/"},
                {"name": "butterfree", "url": f"{BASE}/pokemon/12/"},
            ],
        }
    )

    page = await make_service().list_pokemon(limit=2, offset=10)

    assert page.count == 1351
    assert [(p.id, p.name) for p in page.results] == [(11, "metapod"), (12, "butterfree")]


async def test_get_pokemon_by_type(api, make_service) -> None:
    api.get("/type/water").respond(
        json={"pokemon": [{"pokemon": {"name": "squirtle", "url": f"{BASE}/pokemon/7/"}}]}
    )

    result = await make_service().get_pokemon_by_type(" Water ")

    assert [(p.id, p.name) for p in result] == [(7, "squirtle")]


async def test_invalid_type_is_rejected(api, make_service) -> None:
    with pytest.raises(InvalidIdentifierError):
        await make_service().get_pokemon_by_type("fu3go!")


async def test_species_prefers_spanish_and_cleans_text(api, make_service) -> None:
    api.get("/pokemon-species/25").respond(json=species_payload())

    species = await make_service().get_species(25)

    assert species.display_name == "Pikachu"
    assert species.genus == "Pokémon Ratón"
    assert species.description == "Levanta su cola para vigilar los alrededores."
    assert species.generation == "generation-i"
    assert species.evolution_chain_id == 10


async def test_species_falls_back_to_english(api, make_service) -> None:
    api.get("/pokemon-species/25").respond(json=species_payload(with_spanish=False))

    species = await make_service().get_species(25)

    assert species.display_name == "Pikachu"
    assert species.genus == "Mouse Pokémon"
    assert species.description == "Old English text."


async def test_search_filters_by_name_and_paginates(api, make_service) -> None:
    names = ["pichu", "pikachu", "raichu", "pikipek", "bulbasaur"]
    api.get("/pokemon").respond(
        json={
            "count": len(names),
            "results": [
                {"name": n, "url": f"{BASE}/pokemon/{i + 1}/"} for i, n in enumerate(names)
            ],
        }
    )
    api.get("/pokemon/2").respond(json=pokemon_payload(2, "pikachu"))
    api.get("/pokemon/4").respond(404)  # aparece en el índice pero no tiene detalle

    result = await make_service().search_pokemon("PIK", limit=10)

    assert result.count == 2
    assert [p.name for p in result.results] == ["pikachu"]
    assert result.results[0].sprite == "https://img.test/2.png"


async def test_search_rejects_strange_queries(api, make_service) -> None:
    with pytest.raises(InvalidIdentifierError):
        await make_service().search_pokemon("pika'; DROP TABLE")


async def test_external_calls_are_logged(api, make_service, caplog) -> None:
    api.get("/pokemon/25").respond(json=pokemon_payload())

    with caplog.at_level(logging.INFO, logger="pokedex_api.pokeapi"):
        await make_service().get_pokemon(25)

    record = next(r for r in caplog.records if "PokeAPI GET" in r.getMessage())
    assert "/pokemon/25" in record.getMessage()
    assert "-> 200" in record.getMessage()
    assert "ms" in record.getMessage()


async def test_service_closes_its_http_client(make_service) -> None:
    service = make_service()
    await service.close()

    assert service._client.is_closed


def test_url_without_id_is_an_error() -> None:
    with pytest.raises(PokeAPIError):
        _id_from_url("https://pokeapi.co/api/v2/pokemon/")


# ═════════════════════════════════════════════════════════════
#  Piezas de resiliencia
# ═════════════════════════════════════════════════════════════
def test_ttl_cache_evicts_least_recently_used(clock: FakeClock) -> None:
    cache: TTLCache[int] = TTLCache(max_entries=2, clock=clock)
    cache.set("a", 1, 60)
    cache.set("b", 2, 60)
    cache.get("a")  # "a" pasa a ser la más reciente
    cache.set("c", 3, 60)

    assert cache.get("b") is None
    assert cache.get("a") == 1
    assert len(cache) == 2
    assert (cache.hits, cache.misses) == (2, 1)

    cache.set("d", 4, 0)  # TTL 0: no se guarda
    assert cache.get("d") is None
    cache.clear()
    assert len(cache) == 0


def test_ttl_cache_entries_expire(clock: FakeClock) -> None:
    cache: TTLCache[str] = TTLCache(max_entries=10, clock=clock)
    cache.set("corta", "valor", 5)

    clock.advance(5)

    assert cache.get("corta") is None
    assert len(cache) == 0


def test_circuit_breaker_reopens_if_the_trial_call_fails(clock: FakeClock) -> None:
    breaker = CircuitBreaker(failure_threshold=1, cooldown_seconds=10, clock=clock)
    breaker.record_failure()
    assert not breaker.allow_request()

    clock.advance(10)
    assert breaker.allow_request()  # half_open: se permite una prueba
    breaker.record_failure()
    assert breaker.state == "open"
