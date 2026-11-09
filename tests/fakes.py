"""Datos falsos de PokeAPI compartidos por los tests (ningún test usa la red)."""

import io

from PIL import Image

POKEAPI = "https://pokeapi.co/api/v2"
SPRITES = "https://raw.githubusercontent.com/PokeAPI/sprites/master/sprites/pokemon"
BASE = "https://pokeapi.test/api/v2"


def pokemon_payload(pid: int = 25, name: str = "pikachu", types: tuple = ("electric",)) -> dict:
    stats = ["hp", "attack", "defense", "special-attack", "special-defense", "speed"]
    return {
        "id": pid,
        "name": name,
        "height": 4,
        "weight": 60,
        "base_experience": 112,
        # Desordenados a propósito: el cliente debe ordenarlos por slot.
        "types": [
            {"slot": i + 1, "type": {"name": t, "url": f"{BASE}/type/{i}/"}}
            for i, t in reversed(list(enumerate(types)))
        ],
        "abilities": [
            {"slot": 3, "is_hidden": True, "ability": {"name": "lightning-rod", "url": "x"}},
            {"slot": 1, "is_hidden": False, "ability": {"name": "static", "url": "x"}},
        ],
        "stats": [
            {"base_stat": 10 * (i + 1), "stat": {"name": s, "url": "x"}}
            for i, s in enumerate(stats)
        ],
        "sprites": {
            "front_default": f"https://img.test/{pid}.png",
            "front_shiny": None,
            "other": {"official-artwork": {"front_default": f"https://img.test/art/{pid}.png"}},
        },
        "moves": [{"move": {"name": "se-ignora"}}] * 50,
    }


def species_payload(with_spanish: bool = True) -> dict:
    flavor = [
        {"flavor_text": "Old\nEnglish\ftext.", "language": {"name": "en"}},
        {"flavor_text": "Texto\nantiguo.", "language": {"name": "es"}},
        {
            "flavor_text": "Levanta su cola\npara vigilar\flos alrededores.",
            "language": {"name": "es"},
        },
    ]
    genera = [
        {"genus": "Mouse Pokémon", "language": {"name": "en"}},
        {"genus": "Pokémon Ratón", "language": {"name": "es"}},
    ]
    names = [{"name": "Pikachu", "language": {"name": "es"}}]
    if not with_spanish:
        flavor = [f for f in flavor if f["language"]["name"] == "en"]
        genera = [g for g in genera if g["language"]["name"] == "en"]
        names = []
    return {
        "id": 25,
        "name": "pikachu",
        "names": names,
        "genera": genera,
        "flavor_text_entries": flavor,
        "generation": {"name": "generation-i"},
        "is_legendary": False,
        "is_mythical": False,
        "evolution_chain": {"url": f"{BASE}/evolution-chain/10/"},
    }


def artwork_png() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGBA", (64, 64), (255, 220, 0, 255)).save(buffer, format="PNG")
    return buffer.getvalue()


def with_real_sprites(payload: dict) -> dict:
    pid = payload["id"]
    payload["sprites"]["front_default"] = f"{SPRITES}/{pid}.png"
    payload["sprites"]["other"]["official-artwork"]["front_default"] = (
        f"{SPRITES}/other/official-artwork/{pid}.png"
    )
    payload["species"] = {"name": payload["name"], "url": f"{POKEAPI}/pokemon-species/{pid}/"}
    return payload
