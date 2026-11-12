"""Experiencia, niveles, rivales de la máquina y evoluciones por nivel.

Funciones puras salvo ``level_evolution``, que consulta la cadena evolutiva en PokeAPI.
"""

import random
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Final

from app.models import PokemonDetail
from app.services.battle import Difficulty
from app.services.pokeapi_service import PokeAPIError, PokeAPIService

DEFAULT_LEVEL: Final = 10
MAX_LEVEL: Final = 100
DIFFICULTY_LEVELS: Final = {"easy": -3, "normal": 0, "hard": 3}
WIN_BONUS: Final = 1.5

RIVAL_NAMES: Final = (
    "Lucía", "Marcos", "Irene", "Hugo", "Nerea", "Álex", "Carla", "Dani",
    "Sara", "Iván", "Noa", "Pablo", "Vega", "Leo", "Alba", "Gael",
)  # fmt: skip
THEME_TITLES: Final = {
    "fire": "Entrenador de Fuego", "water": "Nadadora", "grass": "Jardinera",
    "electric": "Ingeniero", "rock": "Montañero", "ground": "Excavadora",
    "psychic": "Médium", "ghost": "Mística", "fighting": "Karateka",
    "poison": "Científico", "bug": "Cazabichos", "flying": "Pajarera",
    "ice": "Esquiadora", "dragon": "Domadragones", "normal": "Entrenadora",
    "fairy": "Hada madrina", "dark": "Pícaro", "steel": "Herrera",
}  # fmt: skip


def xp_to_next(level: int) -> int:
    """Experiencia que falta para pasar de ``level`` al siguiente."""
    return 4 * level + 8


def xp_for_defeat(opponent_level: int) -> int:
    return 10 + 2 * opponent_level


@dataclass(frozen=True)
class LevelResult:
    level: int
    xp: int
    levels_gained: int


def apply_xp(level: int, xp: int, gained: int) -> LevelResult:
    """Suma experiencia y sube los niveles que toque (hasta el 100)."""
    start = level
    xp += gained
    while level < MAX_LEVEL and xp >= xp_to_next(level):
        xp -= xp_to_next(level)
        level += 1
    if level == MAX_LEVEL:
        xp = 0
    return LevelResult(level, xp, level - start)


def battle_xp(defeated_levels: Sequence[int], won: bool) -> int:
    """Experiencia que recibe cada Pokémon que ha participado."""
    total = sum(xp_for_defeat(lv) for lv in defeated_levels)
    return int(total * (WIN_BONUS if won else 1.0))


def fighter_setup(pokemon: PokemonDetail, level: int, **extra: Any) -> dict[str, Any]:
    return {
        "pokemon_id": pokemon.id,
        "name": pokemon.name,
        "types": pokemon.types,
        "level": level,
        "base": pokemon.stats.model_dump(),
        **extra,
    }


def generate_rival(
    rng: random.Random,
    catalog: Sequence[PokemonDetail],
    legendary: set[int],
    *,
    size: int,
    average_level: int,
    difficulty: Difficulty,
    theme: str | None,
) -> dict[str, Any]:
    """Un rival de la máquina con temática, tamaño y nivel acordes al equipo del jugador."""
    pool = [p for p in catalog if p.id not in legendary]
    if theme:
        themed = [p for p in pool if theme in p.types]
        pool = themed if len(themed) >= size else pool
    chosen = rng.sample(pool, k=min(size, len(pool)))
    level = max(2, min(MAX_LEVEL, average_level + DIFFICULTY_LEVELS[difficulty]))
    title = THEME_TITLES.get(theme or "", "Entrenadora")
    return {
        "name": f"{title} {rng.choice(RIVAL_NAMES)}",
        "cpu": difficulty,
        "fighters": [fighter_setup(p, max(2, level + rng.randint(-1, 1))) for p in chosen],
    }


_LEVEL_CONDITION: Final = re.compile(r"^nivel (\d+)$")


async def level_evolution(pokeapi: PokeAPIService, pokemon_id: int) -> tuple[int, str, int] | None:
    """(id, nombre, nivel) de la evolución por nivel de un Pokémon, o None si no tiene o
    no se puede consultar ahora."""
    try:
        species = await pokeapi.get_species(pokemon_id)
        if species.evolution_chain_id is None:
            return None
        chain = await pokeapi.get_evolution_chain(species.evolution_chain_id)
    except PokeAPIError:
        return None
    for step in chain.steps:
        if step.evolves_from == species.name and step.condition:
            match = _LEVEL_CONDITION.match(step.condition)
            if match:
                return step.id, step.name, int(match.group(1))
    return None
