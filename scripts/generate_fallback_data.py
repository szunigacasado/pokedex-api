"""Genera ``app/data/pokemon_fallback.json`` con los 151 Pokémon originales.

Esos datos se usan solo si PokeAPI no está disponible. El script usa el propio
``PokeAPIService``, así que también sirve como prueba real de la integración.

Uso:
    uv run python -m scripts.generate_fallback_data
"""

import asyncio
import json
import os
import sys

# El servicio lee la configuración; para este script no se necesita una clave real.
os.environ.setdefault("SECRET_KEY", "solo-para-generar-datos-de-respaldo-0123456789")

from app.services.pokeapi_service import FALLBACK_DATA_PATH, PokeAPIService

FIRST_GENERATION = range(1, 152)


async def main() -> int:
    service = PokeAPIService()
    try:
        pokemon = await asyncio.gather(*(service.get_pokemon(i) for i in FIRST_GENERATION))
        species = await asyncio.gather(*(service.get_species(i) for i in FIRST_GENERATION))
    finally:
        await service.close()

    data = {
        "source": "https://pokeapi.co/api/v2",
        "description": "Copia de respaldo de la 1.ª generación para cuando PokeAPI no responde.",
        "pokemon": {str(p.id): p.model_dump() for p in pokemon},
        "species": {str(s.id): s.model_dump() for s in species},
    }
    FALLBACK_DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
    FALLBACK_DATA_PATH.write_text(
        json.dumps(data, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8"
    )
    size_kb = FALLBACK_DATA_PATH.stat().st_size / 1024
    print(f"{len(pokemon)} Pokémon guardados en {FALLBACK_DATA_PATH} ({size_kb:.0f} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
