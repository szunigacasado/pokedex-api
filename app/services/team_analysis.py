"""Análisis de un equipo de batalla: estadísticas combinadas y cobertura de tipos.

Todo se calcula con datos y reglas deterministas: la tabla de efectividades de tipos de
la saga (generación 6 en adelante) y las estadísticas base de PokeAPI. Son funciones
puras, sin base de datos ni HTTP.

Cuando se añada la IA, solo **redactará** estas conclusiones; nunca las inventará.
"""

from collections.abc import Sequence
from typing import Final

from app.models import (
    PokemonDetail,
    StatBlock,
    TeamAnalysis,
    TeamCombinedStats,
    TeamMemberAnalysis,
    TypeMatchup,
)
from app.services.card_renderer import type_label

STAT_KEYS: Final = ("hp", "attack", "defense", "special_attack", "special_defense", "speed")
ALL_TYPES: Final = (
    "normal", "fire", "water", "electric", "grass", "ice", "fighting", "poison", "ground",
    "flying", "psychic", "bug", "rock", "ghost", "dragon", "dark", "steel", "fairy",
)  # fmt: skip

# Multiplicador de daño: TYPE_CHART[atacante][defensor]. Solo se listan los valores
# distintos de 1. Tabla oficial desde la generación 6 (con el tipo Hada).
TYPE_CHART: Final[dict[str, dict[str, float]]] = {
    "normal": {"rock": 0.5, "ghost": 0, "steel": 0.5},
    "fire": {"fire": 0.5, "water": 0.5, "grass": 2, "ice": 2, "bug": 2, "rock": 0.5,
             "dragon": 0.5, "steel": 2},
    "water": {"fire": 2, "water": 0.5, "grass": 0.5, "ground": 2, "rock": 2, "dragon": 0.5},
    "electric": {"water": 2, "electric": 0.5, "grass": 0.5, "ground": 0, "flying": 2,
                 "dragon": 0.5},
    "grass": {"fire": 0.5, "water": 2, "grass": 0.5, "poison": 0.5, "ground": 2,
              "flying": 0.5, "bug": 0.5, "rock": 2, "dragon": 0.5, "steel": 0.5},
    "ice": {"fire": 0.5, "water": 0.5, "grass": 2, "ice": 0.5, "ground": 2, "flying": 2,
            "dragon": 2, "steel": 0.5},
    "fighting": {"normal": 2, "ice": 2, "poison": 0.5, "flying": 0.5, "psychic": 0.5,
                 "bug": 0.5, "rock": 2, "ghost": 0, "dark": 2, "steel": 2, "fairy": 0.5},
    "poison": {"grass": 2, "poison": 0.5, "ground": 0.5, "rock": 0.5, "ghost": 0.5,
               "steel": 0, "fairy": 2},
    "ground": {"fire": 2, "electric": 2, "grass": 0.5, "poison": 2, "flying": 0, "bug": 0.5,
               "rock": 2, "steel": 2},
    "flying": {"electric": 0.5, "grass": 2, "fighting": 2, "bug": 2, "rock": 0.5,
               "steel": 0.5},
    "psychic": {"fighting": 2, "poison": 2, "psychic": 0.5, "dark": 0, "steel": 0.5},
    "bug": {"fire": 0.5, "grass": 2, "fighting": 0.5, "poison": 0.5, "flying": 0.5,
            "psychic": 2, "ghost": 0.5, "dark": 2, "steel": 0.5, "fairy": 0.5},
    "rock": {"fire": 2, "ice": 2, "fighting": 0.5, "ground": 0.5, "flying": 2, "bug": 2,
             "steel": 0.5},
    "ghost": {"normal": 0, "psychic": 2, "ghost": 2, "dark": 0.5},
    "dragon": {"dragon": 2, "steel": 0.5, "fairy": 0},
    "dark": {"fighting": 0.5, "psychic": 2, "ghost": 2, "dark": 0.5, "fairy": 0.5},
    "steel": {"fire": 0.5, "water": 0.5, "electric": 0.5, "ice": 2, "rock": 2, "steel": 0.5,
              "fairy": 2},
    "fairy": {"fire": 0.5, "fighting": 2, "poison": 0.5, "dragon": 2, "dark": 2,
              "steel": 0.5},
}  # fmt: skip

STAT_LABELS: Final = {
    "hp": "PS",
    "attack": "Ataque",
    "defense": "Defensa",
    "special_attack": "Ataque especial",
    "special_defense": "Defensa especial",
    "speed": "Velocidad",
}


def es_number(value: float) -> str:
    """Número con coma decimal, como se escribe en español (93,5)."""
    return f"{value:g}".replace(".", ",")


def effectiveness(attacking: str, defending_types: Sequence[str]) -> float:
    """Multiplicador de un ataque contra un Pokémon de uno o dos tipos (se multiplican)."""
    multiplier = 1.0
    for defending in defending_types:
        multiplier *= TYPE_CHART.get(attacking, {}).get(defending, 1.0)
    return multiplier


def defensive_matchups(members_types: Sequence[Sequence[str]]) -> list[TypeMatchup]:
    """Cómo recibe el equipo cada tipo de ataque: cuántos miembros son débiles, resisten o
    son inmunes. Ordenado de más peligroso a menos."""
    matchups = []
    for attacking in ALL_TYPES:
        multipliers = [effectiveness(attacking, types) for types in members_types]
        matchups.append(
            TypeMatchup(
                type=attacking,
                weak=sum(m > 1 for m in multipliers),
                resist=sum(0 < m < 1 for m in multipliers),
                immune=sum(m == 0 for m in multipliers),
                multipliers=multipliers,
            )
        )
    return sorted(matchups, key=lambda m: (-(m.weak - m.resist - m.immune), -m.weak, m.type))


def offensive_coverage(members_types: Sequence[Sequence[str]]) -> list[str]:
    """Tipos defensores a los que el equipo puede golpear de forma supereficaz usando los
    tipos de sus propios miembros (ataques con bonificación STAB)."""
    attacking_types = {t for types in members_types for t in types}
    return [
        defending
        for defending in ALL_TYPES
        if any(TYPE_CHART.get(a, {}).get(defending, 1.0) > 1 for a in attacking_types)
    ]


def _stat_block(values: dict[str, float]) -> StatBlock:
    return StatBlock(**{k: values[k] for k in STAT_KEYS})


def analyze_team(team_id: int, name: str, members: Sequence[PokemonDetail]) -> TeamAnalysis:
    members_types = [m.types for m in members]
    totals = {k: sum(getattr(m.stats, k) for m in members) for k in STAT_KEYS}
    count = len(members) or 1
    averages = {k: round(v / count, 1) for k, v in totals.items()}

    defensive = defensive_matchups(members_types)
    covered = offensive_coverage(members_types)
    # Debilidad compartida: al menos la mitad del equipo es débil y no hay más miembros
    # que la resistan que miembros débiles.
    threshold = max(2, (len(members) + 1) // 2)
    shared = [m.type for m in defensive if m.weak >= threshold and m.weak > m.resist + m.immune]

    analysis_members = [
        TeamMemberAnalysis(
            pokemon_id=m.id,
            name=m.name,
            types=m.types,
            stats=_stat_block({k: getattr(m.stats, k) for k in STAT_KEYS}),
            base_stat_total=m.stats.total,
        )
        for m in members
    ]

    return TeamAnalysis(
        team_id=team_id,
        name=name,
        members=analysis_members,
        combined=TeamCombinedStats(
            total=_stat_block(totals),
            average=_stat_block(averages),
            base_stat_total=sum(m.stats.total for m in members),
            average_base_stat_total=round(sum(m.stats.total for m in members) / count, 1),
        ),
        defensive=defensive,
        shared_weaknesses=shared,
        super_effective_against=covered,
        not_covered=[t for t in ALL_TYPES if t not in covered],
        highlights=_highlights(analysis_members, averages, shared, covered, len(members)),
    )


def _highlights(
    members: Sequence[TeamMemberAnalysis],
    averages: dict[str, float],
    shared: Sequence[str],
    covered: Sequence[str],
    size: int,
) -> list[str]:
    """Conclusiones en español, generadas con reglas a partir de los datos."""
    if not members:
        return ["El equipo está vacío."]

    notes = []
    best = max(STAT_KEYS, key=lambda k: averages[k])
    worst = min(STAT_KEYS, key=lambda k: averages[k])
    notes.append(
        f"Punto fuerte: {STAT_LABELS[best]} (media {es_number(averages[best])}). "
        f"Punto débil: {STAT_LABELS[worst]} (media {es_number(averages[worst])})."
    )

    fastest = max(members, key=lambda m: m.stats.speed)
    notes.append(
        f"El más rápido es {fastest.name.title()} ({es_number(fastest.stats.speed)} de velocidad)."
    )

    if shared:
        names = ", ".join(type_label(t) for t in shared)
        notes.append(f"Debilidades compartidas: {names}. Conviene un miembro que las resista.")
    else:
        notes.append("Sin debilidades compartidas graves: las debilidades están repartidas.")

    notes.append(
        f"Cobertura ofensiva: supereficaz contra {len(covered)} de {len(ALL_TYPES)} tipos."
    )
    if size < 6:
        notes.append(f"Quedan {6 - size} huecos libres en el equipo.")
    return notes
