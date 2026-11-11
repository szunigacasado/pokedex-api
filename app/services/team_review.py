"""Nota de un equipo (0-10), calculada por el código con la tabla de tipos.

La IA nunca pone la nota: la recibe ya calculada y, como mucho, la explica. Así dos
revisiones del mismo equipo siempre dan el mismo número y se puede justificar cada punto.

Reparto de los 10 puntos:

- **Defensa (4):** se resta por cada tipo de ataque al que el equipo es más débil que
  resistente, y el doble si es una debilidad compartida por medio equipo.
- **Ataque (3):** proporción de los 18 tipos a los que el equipo golpea con daño doble.
- **Estadísticas (2):** media de estadísticas base totales de los miembros (600 = máximo).
- **Equipo completo (1):** cuántos de los 6 huecos están ocupados.
"""

from typing import Final

from app.models import ReviewPart, TeamAnalysis, TeamReview
from app.services.card_renderer import type_label
from app.services.team_analysis import ALL_TYPES, es_number

MAX_DEFENSE: Final = 4.0
MAX_OFFENSE: Final = 3.0
MAX_STATS: Final = 2.0
MAX_SIZE: Final = 1.0
STRONG_TOTAL: Final = 600


def _grade_label(score: float) -> str:
    if score >= 9:
        return "Sobresaliente"
    if score >= 7:
        return "Notable"
    if score >= 5:
        return "Aprobado"
    return "Necesita mejorar"


def _round(value: float) -> float:
    return round(value + 1e-9, 1)


def review_team(analysis: TeamAnalysis) -> TeamReview:
    size = len(analysis.members)
    if size == 0:
        return TeamReview(
            team_id=analysis.team_id,
            score=0,
            grade="Sin miembros",
            parts=[],
            strengths=[],
            weaknesses=["El equipo está vacío: añade Pokémon de tu Pokédex."],
        )

    # ── Defensa ───────────────────────────────────────────────
    problems = [m for m in analysis.defensive if m.weak > m.resist + m.immune]
    shared = set(analysis.shared_weaknesses)
    penalty = sum(1.0 if m.type in shared else 0.5 for m in problems)
    defense = max(0.0, MAX_DEFENSE - penalty)
    if problems:
        names = ", ".join(type_label(m.type) for m in problems[:4])
        defense_detail = f"Más débil que resistente a: {names}."
    else:
        defense_detail = "Ningún tipo de ataque le hace más daño del que resiste."

    # ── Ataque ────────────────────────────────────────────────
    covered = len(analysis.super_effective_against)
    offense = MAX_OFFENSE * covered / len(ALL_TYPES)
    offense_detail = f"Golpea con daño doble a {covered} de {len(ALL_TYPES)} tipos."

    # ── Estadísticas ──────────────────────────────────────────
    average = analysis.combined.average_base_stat_total
    stats = MAX_STATS * min(1.0, average / STRONG_TOTAL)
    stats_detail = f"Media de estadísticas base: {es_number(average)} (600 es de élite)."

    # ── Tamaño ────────────────────────────────────────────────
    size_score = MAX_SIZE * size / 6
    size_detail = "Equipo completo." if size == 6 else f"{size} de 6 huecos ocupados."

    parts = [
        ReviewPart(
            key="defense",
            label="Defensa",
            score=_round(defense),
            max=MAX_DEFENSE,
            detail=defense_detail,
        ),
        ReviewPart(
            key="offense",
            label="Ataque",
            score=_round(offense),
            max=MAX_OFFENSE,
            detail=offense_detail,
        ),
        ReviewPart(
            key="stats",
            label="Estadísticas",
            score=_round(stats),
            max=MAX_STATS,
            detail=stats_detail,
        ),
        ReviewPart(
            key="size",
            label="Equipo completo",
            score=_round(size_score),
            max=MAX_SIZE,
            detail=size_detail,
        ),
    ]
    score = _round(defense + offense + stats + size_score)

    strengths: list[str] = []
    weaknesses: list[str] = []
    for part in parts:
        ratio = part.score / part.max
        if ratio >= 0.75:
            strengths.append(f"{part.label}: {part.detail}")
        elif ratio < 0.5:
            weaknesses.append(f"{part.label}: {part.detail}")
    for t in analysis.shared_weaknesses[:2]:
        weaknesses.append(f"Medio equipo o más es débil a {type_label(t)}.")

    return TeamReview(
        team_id=analysis.team_id,
        score=score,
        grade=_grade_label(score),
        parts=parts,
        strengths=strengths[:4],
        weaknesses=weaknesses[:4],
    )
