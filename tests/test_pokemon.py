"""Tests de los endpoints de Pokémon (búsqueda, detalle y ficha descargable).

PokeAPI se simula con ``respx``: los tests no usan la red.
"""

import io
from collections.abc import Iterator

import httpx
import pytest
import respx
from fastapi.testclient import TestClient
from PIL import Image

from app.models import PokemonAbility, PokemonDetail, PokemonSpecies, PokemonSprites, PokemonStats
from app.services.card_renderer import _wrap, card_filename, render_card, type_label
from app.services.pokeapi_service import PokeAPIService
from tests.fakes import (
    POKEAPI,
    SPRITES,
    artwork_png,
    pokemon_payload,
    species_payload,
    with_real_sprites,
)

SEARCH = "/api/v1/pokemon/search"


@pytest.fixture
def pokeapi(client: TestClient) -> PokeAPIService:
    """El servicio real de la aplicación, sin esperas entre reintentos."""
    service: PokeAPIService = client.app.state.pokeapi

    async def no_sleep(_: float) -> None:
        return None

    service._sleep = no_sleep
    return service


@pytest.fixture
def api() -> Iterator[respx.MockRouter]:
    with respx.mock(assert_all_called=False) as router:
        yield router


@pytest.fixture
def pikachu(api: respx.MockRouter) -> None:
    api.get(f"{POKEAPI}/pokemon/pikachu").respond(json=with_real_sprites(pokemon_payload()))
    api.get(f"{POKEAPI}/pokemon/25").respond(json=with_real_sprites(pokemon_payload()))
    api.get(f"{POKEAPI}/pokemon-species/pikachu").respond(json=species_payload())
    api.get(f"{POKEAPI}/ability/static").respond(
        json={"names": [{"name": "Elec. Estática", "language": {"name": "es"}}]}
    )
    api.get(f"{POKEAPI}/ability/lightning-rod").respond(500)
    api.get(f"{SPRITES}/other/official-artwork/25.png").respond(
        content=artwork_png(), headers={"Content-Type": "image/png"}
    )


# ═════════════════════════════════════════════════════════════
#  Autenticación
# ═════════════════════════════════════════════════════════════
@pytest.mark.parametrize(
    "path", [SEARCH, "/api/v1/pokemon/pikachu", "/api/v1/pokemon/pikachu/card"]
)
def test_pokemon_endpoints_require_authentication(client: TestClient, path: str) -> None:
    assert client.get(path).status_code == 401


# ═════════════════════════════════════════════════════════════
#  Búsqueda
# ═════════════════════════════════════════════════════════════
def test_search_returns_simplified_list(
    client: TestClient, auth_headers: dict, api: respx.MockRouter, pikachu
) -> None:
    api.get(f"{POKEAPI}/pokemon").respond(
        json={
            "count": 3,
            "results": [
                {"name": "bulbasaur", "url": f"{POKEAPI}/pokemon/1/"},
                {"name": "pikachu", "url": f"{POKEAPI}/pokemon/25/"},
                {"name": "raichu", "url": f"{POKEAPI}/pokemon/26/"},
            ],
        }
    )

    response = client.get(SEARCH, params={"name": "PIKA"}, headers=auth_headers)

    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 1
    assert body["results"] == [
        {"id": 25, "name": "pikachu", "sprite": f"{SPRITES}/25.png", "types": ["electric"]}
    ]


@pytest.mark.parametrize(
    "params", [{"limit": 0}, {"limit": 101}, {"offset": -1}, {"name": ""}, {"name": "x" * 51}]
)
def test_search_validates_parameters(client: TestClient, auth_headers: dict, params: dict) -> None:
    assert client.get(SEARCH, params=params, headers=auth_headers).status_code == 422


def test_search_rejects_strange_text(client: TestClient, auth_headers: dict) -> None:
    response = client.get(SEARCH, params={"name": "pika'; --"}, headers=auth_headers)

    assert response.status_code == 422


# ═════════════════════════════════════════════════════════════
#  Detalle
# ═════════════════════════════════════════════════════════════
def test_get_pokemon_detail_with_species(client: TestClient, auth_headers: dict, pikachu) -> None:
    response = client.get("/api/v1/pokemon/pikachu", headers=auth_headers)

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == 25
    assert body["types"] == ["electric"]
    assert set(body["stats"]) == {
        "hp",
        "attack",
        "defense",
        "special_attack",
        "special_defense",
        "speed",
    }
    assert body["abilities"][0] == {"name": "static", "is_hidden": False}
    assert body["sprites"]["official_artwork"].endswith("/official-artwork/25.png")
    assert body["species"]["genus"] == "Pokémon Ratón"
    assert body["species"]["description"].startswith("Levanta su cola")


def test_detail_survives_missing_species(
    client: TestClient, auth_headers: dict, api: respx.MockRouter
) -> None:
    api.get(f"{POKEAPI}/pokemon/25").respond(json=with_real_sprites(pokemon_payload()))
    api.get(f"{POKEAPI}/pokemon-species/pikachu").respond(404)

    response = client.get("/api/v1/pokemon/25", headers=auth_headers)

    assert response.status_code == 200
    assert response.json()["species"] is None


def test_unknown_pokemon_returns_404(client: TestClient, auth_headers: dict, api) -> None:
    api.get(f"{POKEAPI}/pokemon/missingno").respond(404)

    response = client.get("/api/v1/pokemon/missingno", headers=auth_headers)

    assert response.status_code == 404
    assert response.json() == {"detail": "Pokémon no encontrado: missingno"}


@pytest.mark.parametrize("identifier", ["pika%20chu", "0", "100001", "pika_chu", "%27or%201%3D1"])
def test_malformed_identifier_returns_422(
    client: TestClient, auth_headers: dict, api: respx.MockRouter, identifier: str
) -> None:
    response = client.get(f"/api/v1/pokemon/{identifier}", headers=auth_headers)

    assert response.status_code == 422
    assert not api.calls


def test_pokeapi_down_without_fallback_returns_503(
    client: TestClient, auth_headers: dict, api: respx.MockRouter, pokeapi
) -> None:
    api.get(f"{POKEAPI}/pokemon/9999").mock(side_effect=httpx.ConnectTimeout("caída"))

    response = client.get("/api/v1/pokemon/9999", headers=auth_headers)

    assert response.status_code == 503
    assert response.headers["retry-after"] == "30"
    assert "9999" not in response.text  # el mensaje no da detalles internos


def test_pokeapi_down_uses_fallback_data(
    client: TestClient, auth_headers: dict, api: respx.MockRouter, pokeapi
) -> None:
    api.route(host="pokeapi.co").mock(side_effect=httpx.ConnectTimeout("caída"))

    response = client.get("/api/v1/pokemon/mewtwo", headers=auth_headers)

    assert response.status_code == 200
    assert response.json()["species"]["is_legendary"] is True


def test_unexpected_pokeapi_answer_returns_502(
    client: TestClient, auth_headers: dict, api: respx.MockRouter
) -> None:
    api.get(f"{POKEAPI}/pokemon/25").respond(418)

    assert client.get("/api/v1/pokemon/25", headers=auth_headers).status_code == 502


# ═════════════════════════════════════════════════════════════
#  Ficha descargable
# ═════════════════════════════════════════════════════════════
def test_card_is_downloaded_as_png(client: TestClient, auth_headers: dict, pikachu) -> None:
    response = client.get("/api/v1/pokemon/pikachu/card", headers=auth_headers)

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.headers["content-disposition"] == (
        'attachment; filename="0025-pikachu-ficha.png"'
    )
    image = Image.open(io.BytesIO(response.content))
    assert image.format == "PNG"
    assert image.size == (600, 840)


def test_card_is_downloaded_as_pdf(client: TestClient, auth_headers: dict, pikachu) -> None:
    response = client.get(
        "/api/v1/pokemon/pikachu/card", params={"format": "pdf"}, headers=auth_headers
    )

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["content-disposition"].endswith('.pdf"')
    assert response.content.startswith(b"%PDF")


def test_card_without_artwork_still_renders(
    client: TestClient, auth_headers: dict, api: respx.MockRouter
) -> None:
    api.get(f"{POKEAPI}/pokemon/25").respond(json=with_real_sprites(pokemon_payload()))
    api.get(f"{POKEAPI}/pokemon-species/pikachu").respond(404)
    api.get(url__startswith=f"{POKEAPI}/ability/").respond(404)
    api.get(f"{SPRITES}/other/official-artwork/25.png").respond(500)

    response = client.get("/api/v1/pokemon/25/card", headers=auth_headers)

    assert response.status_code == 200
    assert Image.open(io.BytesIO(response.content)).size == (600, 840)


def test_card_rejects_unknown_format(client: TestClient, auth_headers: dict) -> None:
    response = client.get(
        "/api/v1/pokemon/pikachu/card", params={"format": "gif"}, headers=auth_headers
    )

    assert response.status_code == 422


# ═════════════════════════════════════════════════════════════
#  Descarga de imágenes (protección SSRF)
# ═════════════════════════════════════════════════════════════
@pytest.mark.parametrize(
    "url",
    [
        None,
        "http://raw.githubusercontent.com/PokeAPI/sprites/master/1.png",  # sin HTTPS
        "https://169.254.169.254/latest/meta-data/",  # metadatos de la nube
        "https://localhost/admin.png",
        "https://raw.githubusercontent.com/otro-usuario/repo/malware.png",
        "https://evil.example/PokeAPI/sprites/1.png",
    ],
)
async def test_images_outside_allowlist_are_never_fetched(url: str | None) -> None:
    service = PokeAPIService()
    with respx.mock(assert_all_called=False) as router:
        assert await service.get_image(url) is None
        assert not router.calls
    await service.close()


async def test_images_are_cached_and_validated() -> None:
    service = PokeAPIService()
    ok_url = f"{SPRITES}/1.png"
    with respx.mock() as router:
        route = router.get(ok_url).respond(
            content=artwork_png(), headers={"Content-Type": "image/png"}
        )
        router.get(f"{SPRITES}/html.png").respond(
            text="<html>", headers={"Content-Type": "text/html"}
        )
        router.get(f"{SPRITES}/big.png").respond(
            content=b"0" * (2 * 1024 * 1024 + 1), headers={"Content-Type": "image/png"}
        )
        router.get(f"{SPRITES}/down.png").mock(side_effect=httpx.ConnectError("sin red"))

        assert await service.get_image(ok_url) == await service.get_image(ok_url)
        assert route.call_count == 1
        assert await service.get_image(f"{SPRITES}/html.png") is None
        assert await service.get_image(f"{SPRITES}/big.png") is None
        assert await service.get_image(f"{SPRITES}/down.png") is None
    await service.close()


# ═════════════════════════════════════════════════════════════
#  Dibujo de la ficha
# ═════════════════════════════════════════════════════════════
def make_detail(**overrides) -> PokemonDetail:
    data = {
        "id": 150,
        "name": "mewtwo",
        "height": 20,
        "weight": 1220,
        "types": ["psychic"],
        "abilities": [PokemonAbility(name="pressure")],
        "stats": PokemonStats(
            hp=106, attack=110, defense=90, special_attack=154, special_defense=90, speed=130
        ),
        "sprites": PokemonSprites(),
    }
    return PokemonDetail(**(data | overrides))


def test_card_handles_legendary_unknown_types_and_broken_images() -> None:
    species = PokemonSpecies(
        id=150, name="mewtwo", display_name="Mewtwo", is_legendary=True, description="Texto " * 200
    )
    card = render_card(make_detail(types=["shadow"]), species, artwork=b"no es una imagen")

    assert card.size == (600, 840)
    assert type_label("shadow") == "Shadow"
    assert type_label("dragon") == "Dragón"


def test_card_marks_mythical_pokemon() -> None:
    species = PokemonSpecies(id=151, name="mew", display_name="Mew", is_mythical=True)

    assert render_card(make_detail(id=151, name="mew"), species).size == (600, 840)


def test_long_text_is_truncated_with_ellipsis() -> None:
    from app.services.card_renderer import _font

    lines = _wrap("palabra " * 100, _font(16), 300, 2)

    assert len(lines) == 2
    assert lines[-1].endswith("…")
    assert _wrap("lo que sea", _font(16), 300, 0) == []


def test_card_filename_is_safe() -> None:
    detail = make_detail(name='mr-mime"; rm -rf /')

    assert card_filename(detail, "png") == "0150-mr-mimerm-rf-ficha.png"


# ═════════════════════════════════════════════════════════════
#  Catálogo de Kanto
# ═════════════════════════════════════════════════════════════
def test_kanto_catalog_comes_from_local_data_in_one_request(
    client: TestClient, auth_headers: dict
) -> None:
    """La web pinta la Pokédex con una sola petición y sin gastar llamadas a PokeAPI."""
    with respx.mock(assert_all_called=False) as pokeapi:
        response = client.get("/api/v1/pokemon/kanto", headers=auth_headers)
        assert not pokeapi.calls

    assert response.status_code == 200
    catalog = response.json()
    assert len(catalog) == 151
    assert [p["id"] for p in catalog] == list(range(1, 152))
    bulbasaur, pikachu = catalog[0], catalog[24]
    assert bulbasaur["name"] == "bulbasaur"
    assert bulbasaur["types"] == ["grass", "poison"]
    assert bulbasaur["total"] == 318
    assert pikachu["genus"] == "Pokémon Ratón"
    assert pikachu["artwork"].endswith("/official-artwork/25.png")
    assert catalog[149]["is_legendary"] and catalog[150]["is_mythical"]  # Mewtwo y Mew
    assert response.headers["cache-control"] == "private, max-age=3600"


def test_kanto_catalog_requires_authentication(client: TestClient) -> None:
    assert client.get("/api/v1/pokemon/kanto").status_code == 401
