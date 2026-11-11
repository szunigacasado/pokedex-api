"""Recomendaciones del Profesor Oak: Pokémon **concretos** de Kanto que arreglan los
puntos débiles de un equipo.

Es código puro, sin IA: la IA redacta el consejo, pero qué Pokémon cubre qué debilidad
lo decide la tabla de tipos. Así una recomendación nunca puede ser falsa.

- **Defensa:** para cada tipo de ataque que hace daño doble a más miembros de los que lo
  resisten, Pokémon que lo resisten o son inmunes.
- **Ataque:** para cada tipo al que el equipo no puede golpear con daño doble, Pokémon
  cuyo propio tipo sí puede.

Se priorizan los que el entrenador ya tiene en su Pokédex (los puede meter en el equipo
al momento) y, después, los de más estadística total.
"""

from collections.abc import Iterable, Mapping
from typing import Final

from app.models import PokemonDetail, Recommendation, RecommendedPokemon, TeamAnalysis
from app.services.card_renderer import type_label
from app.services.team_analysis import ALL_TYPES, effectiveness

MAX_GOALS: Final = 4
PER_GOAL: Final = 4
_FRACTIONS: Final = {0.25: "¼", 0.5: "½"}


def multiplier_label(value: float) -> str:
    return f"×{_FRACTIONS.get(value, f'{value:g}')}"


def _total(pokemon: PokemonDetail) -> int:
    return sum(pokemon.stats.model_dump().values())


def _pick(
    candidates: Iterable[tuple[PokemonDetail, str]],
    owned: Mapping[int, bool],
) -> list[RecommendedPokemon]:
    ranked = sorted(candidates, key=lambda c: (c[0].id not in owned, -_total(c[0]), c[0].id))
    return [
        RecommendedPokemon(
            id=p.id,
            name=p.name,
            sprite=p.sprites.front_default,
            types=p.types,
            total=_total(p),
            in_pokedex=p.id in owned,
            captured=owned.get(p.id, False),
            reason=reason,
        )
        for p, reason in ranked[:PER_GOAL]
    ]


def recommend(
    analysis: TeamAnalysis,
    catalog: Iterable[PokemonDetail],
    owned: Mapping[int, bool],
) -> list[Recommendation]:
    """``owned`` es {pokemon_id: capturado} de la Pokédex del entrenador."""
    catalog = list(catalog)
    in_team = {m.pokemon_id for m in analysis.members}
    available = [p for p in catalog if p.id not in in_team]
    size = len(analysis.members)
    recs: list[Recommendation] = []

    problems = [m for m in analysis.defensive if m.weak > m.resist + m.immune][:2]
    for problem in problems:
        attack = type_label(problem.type)
        covering = problem.resist + problem.immune
        candidates = []
        for p in available:
            multiplier = effectiveness(problem.type, p.types)
            if multiplier < 1:
                reason = (
                    f"Inmune a {attack}"
                    if multiplier == 0
                    else f"Resiste {attack} ({multiplier_label(multiplier)})"
                )
                candidates.append((p, reason))
        recs.append(
            Recommendation(
                kind="defense",
                target_type=problem.type,
                title=f"Cubrir la debilidad a {attack}",
                detail=(
                    f"{problem.weak} de {size} miembros reciben daño doble de {attack} y "
                    + ("ninguno lo resiste." if covering == 0 else f"solo {covering} lo resiste.")
                ),
                useful_types=[t for t in ALL_TYPES if effectiveness(problem.type, [t]) < 1],
                pokemon=_pick(candidates, owned),
            )
        )

    for target in analysis.not_covered[: MAX_GOALS - len(recs)]:
        defender = type_label(target)
        attackers = [t for t in ALL_TYPES if effectiveness(t, [target]) > 1]
        candidates = []
        for p in available:
            hits = [t for t in p.types if t in attackers]
            if hits:
                candidates.append((p, f"Su tipo {type_label(hits[0])} hace ×2 a {defender}"))
        recs.append(
            Recommendation(
                kind="offense",
                target_type=target,
                title=f"Poder golpear a {defender}",
                detail=f"Ningún miembro le hace daño doble a {defender}.",
                useful_types=attackers,
                pokemon=_pick(candidates, owned),
            )
        )
    return recs
