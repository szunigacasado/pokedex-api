"""Cálculo de las estadísticas de una Pokédex.

Son funciones puras, sin base de datos ni HTTP, para poder probar cada caso límite de
forma aislada: Pokédex vacía, empates o cambio de día según la zona horaria.
"""

from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from itertools import pairwise
from zoneinfo import ZoneInfo

from app.models import PokedexEntry


@dataclass(frozen=True, slots=True)
class Streaks:
    current: int
    longest: int


def capture_streaks(capture_days: Iterable[date], today: date) -> Streaks:
    """Rachas de días consecutivos con al menos una captura.

    - **Racha actual**: días seguidos con capturas que terminan hoy. Si hoy todavía no se
      ha capturado nada pero ayer sí, la racha sigue viva (el día aún no ha terminado).
      Si el último día con capturas es anterior a ayer, la racha actual es 0.
    - **Racha más larga**: el mayor número de días consecutivos con capturas.
    """
    days = sorted(set(capture_days))
    if not days:
        return Streaks(current=0, longest=0)

    one_day = timedelta(days=1)
    longest = run = 1
    for previous, current in pairwise(days):
        run = run + 1 if current - previous == one_day else 1
        longest = max(longest, run)

    if days[-1] < today - one_day:
        return Streaks(current=0, longest=longest)

    current = 1
    for later, earlier in pairwise(reversed(days)):
        if later - earlier != one_day:
            break
        current += 1
    return Streaks(current=current, longest=longest)


def most_common_type(type_lists: Iterable[Sequence[str]]) -> tuple[str | None, dict[str, int]]:
    """Tipo más frecuente y distribución completa, ordenada de más a menos frecuente.

    En caso de empate gana el primero en orden alfabético, para que el resultado sea
    siempre el mismo con los mismos datos.
    """
    counter = Counter(t for types in type_lists for t in types)
    ranking = sorted(counter.items(), key=lambda item: (-item[1], item[0]))
    distribution = dict(ranking)
    return (ranking[0][0] if ranking else None), distribution


def local_day(moment: datetime, tz: ZoneInfo) -> date:
    return moment.astimezone(tz).date()


@dataclass(frozen=True, slots=True)
class PokedexSummary:
    total: int
    captured: int
    favorites: int
    completion_percentage: float
    most_common_type: str | None
    type_distribution: dict[str, int]
    streaks: Streaks
    first_capture: datetime | None
    last_capture: datetime | None


def summarize(entries: Sequence[PokedexEntry], tz: ZoneInfo, now: datetime) -> PokedexSummary:
    """Resume una Pokédex.

    ``completion_percentage`` sigue la definición del enunciado: el porcentaje de los
    Pokémon de la Pokédex que ya están capturados (89 de 150 → 59,3 %).
    """
    total = len(entries)
    captured = [e for e in entries if e.is_captured]
    capture_dates = sorted(e.capture_date for e in captured if e.capture_date)
    top_type, distribution = most_common_type(e.pokemon_types for e in entries)

    return PokedexSummary(
        total=total,
        captured=len(captured),
        favorites=sum(e.favorite for e in entries),
        completion_percentage=round(100 * len(captured) / total, 1) if total else 0.0,
        most_common_type=top_type,
        type_distribution=distribution,
        streaks=capture_streaks((local_day(d, tz) for d in capture_dates), local_day(now, tz)),
        first_capture=capture_dates[0] if capture_dates else None,
        last_capture=capture_dates[-1] if capture_dates else None,
    )
