"""Tests de las estadísticas de la Pokédex."""

from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
import pytest
import respx
from fastapi.testclient import TestClient
from sqlmodel import Session

from app.models import PokedexEntry, User, utc_now
from app.services.stats import Streaks, capture_streaks, most_common_type, summarize
from tests.fakes import POKEAPI

STATS = "/api/v1/pokedex/stats"
TODAY = date(2026, 11, 9)


def days_ago(*offsets: int) -> list[date]:
    return [TODAY - timedelta(days=n) for n in offsets]


# ═════════════════════════════════════════════════════════════
#  Rachas de capturas
# ═════════════════════════════════════════════════════════════
@pytest.mark.parametrize(
    ("days", "expected"),
    [
        ([], Streaks(0, 0)),
        (days_ago(0), Streaks(1, 1)),
        (days_ago(0, 1, 2), Streaks(3, 3)),
        # Hoy aún no ha capturado, pero ayer sí: la racha sigue viva
        (days_ago(1, 2), Streaks(2, 2)),
        # La última captura fue anteayer: racha rota
        (days_ago(2, 3, 4), Streaks(0, 3)),
        # Racha antigua más larga que la actual
        (days_ago(0, 1, 5, 6, 7, 8), Streaks(2, 4)),
        # Varias capturas el mismo día cuentan como un solo día
        (days_ago(0, 0, 0, 1), Streaks(2, 2)),
    ],
)
def test_capture_streaks(days: list[date], expected: Streaks) -> None:
    assert capture_streaks(days, TODAY) == expected


def test_most_common_type_breaks_ties_alphabetically() -> None:
    top, distribution = most_common_type([["water"], ["fire", "flying"], ["fire"], ["water"]])

    assert top == "fire"
    assert distribution == {"fire": 2, "water": 2, "flying": 1}
    assert list(distribution) == ["fire", "water", "flying"]


def test_most_common_type_of_nothing() -> None:
    assert most_common_type([]) == (None, {})


def make_entry(pid: int, captured_at: datetime | None = None, **fields) -> PokedexEntry:
    return PokedexEntry(
        owner_id=1,
        pokemon_id=pid,
        pokemon_name=f"pokemon-{pid}",
        pokemon_types=fields.pop("types", ["water"]),
        is_captured=captured_at is not None,
        capture_date=captured_at,
        **fields,
    )


def test_completion_follows_the_statement_example() -> None:
    """El ejemplo del enunciado: 89 capturados de 150 → 59,3 %."""
    now = datetime(2026, 11, 9, 12, tzinfo=UTC)
    entries = [make_entry(i, now) for i in range(89)] + [make_entry(i) for i in range(89, 150)]

    summary = summarize(entries, ZoneInfo("UTC"), now)

    assert (summary.total, summary.captured) == (150, 89)
    assert summary.completion_percentage == 59.3


def test_summary_of_an_empty_pokedex() -> None:
    summary = summarize([], ZoneInfo("UTC"), utc_now())

    assert summary.completion_percentage == 0.0
    assert summary.most_common_type is None
    assert summary.first_capture is None and summary.last_capture is None
    assert summary.streaks == Streaks(0, 0)


def test_day_boundaries_depend_on_the_timezone() -> None:
    """23:30 UTC del día 7 y 00:30 UTC del día 8 son dos días en UTC, pero el mismo día
    (el 8) en Madrid."""
    now = datetime(2026, 11, 9, 10, tzinfo=UTC)
    entries = [
        make_entry(1, datetime(2026, 11, 7, 23, 30, tzinfo=UTC)),
        make_entry(2, datetime(2026, 11, 8, 0, 30, tzinfo=UTC)),
    ]

    assert summarize(entries, ZoneInfo("UTC"), now).streaks == Streaks(2, 2)
    assert summarize(entries, ZoneInfo("Europe/Madrid"), now).streaks == Streaks(1, 1)


# ═════════════════════════════════════════════════════════════
#  Endpoint
# ═════════════════════════════════════════════════════════════
@pytest.fixture
def api() -> Iterator[respx.MockRouter]:
    with respx.mock(assert_all_called=False) as router:
        router.get(f"{POKEAPI}/pokemon-species", params={"limit": 1}).respond(
            json={"count": 1025, "results": []}
        )
        yield router


@pytest.fixture
def stored(session: Session, test_user: dict):
    def _store(pid: int, captured_days_ago: int | None = None, **fields) -> None:
        captured_at = (
            utc_now() - timedelta(days=captured_days_ago) if captured_days_ago is not None else None
        )
        entry = make_entry(pid, captured_at, **fields)
        entry.owner_id = test_user["id"]
        session.add(entry)
        session.commit()

    return _store


def test_stats_require_authentication(client: TestClient) -> None:
    assert client.get(STATS).status_code == 401


def test_stats_of_an_empty_pokedex(client: TestClient, auth_headers: dict, api) -> None:
    response = client.get(STATS, headers=auth_headers)

    assert response.status_code == 200
    body = response.json()
    assert body["total_pokemon"] == 0
    assert body["completion_percentage"] == 0.0
    assert body["most_common_type"] is None
    assert body["capture_streak_days"] == 0
    assert body["national_dex_total"] == 1025
    assert body["national_dex_percentage"] == 0.0


def test_stats_have_the_fields_of_the_statement(
    client: TestClient, auth_headers: dict, api, stored
) -> None:
    stored(7, captured_days_ago=0, favorite=True)
    stored(9, captured_days_ago=1, types=["water"])
    stored(25, captured_days_ago=2, types=["electric"])
    stored(6, captured_days_ago=10, types=["fire", "flying"], favorite=True)
    stored(150, types=["psychic"])

    body = client.get(STATS, headers=auth_headers).json()

    assert {
        k: body[k]
        for k in (
            "total_pokemon",
            "captured",
            "favorites",
            "completion_percentage",
            "most_common_type",
            "capture_streak_days",
        )
    } == {
        "total_pokemon": 5,
        "captured": 4,
        "favorites": 2,
        "completion_percentage": 80.0,
        "most_common_type": "water",
        "capture_streak_days": 3,
    }
    assert body["longest_streak_days"] == 3
    assert body["type_distribution"] == {
        "water": 2,
        "electric": 1,
        "fire": 1,
        "flying": 1,
        "psychic": 1,
    }
    assert body["national_dex_percentage"] == round(100 * 4 / 1025, 1)
    assert body["first_capture_date"] < body["last_capture_date"]
    assert body["timezone"] == "UTC"


def test_stats_only_count_my_pokedex(
    client: TestClient, auth_headers: dict, api, session: Session, stored
) -> None:
    stored(25, captured_days_ago=0)
    gary = User(username="gary", email="gary@kanto.dev", hashed_password="x")
    session.add(gary)
    session.commit()
    other = make_entry(7, utc_now())
    other.owner_id = gary.id
    session.add(other)
    session.commit()

    assert client.get(STATS, headers=auth_headers).json()["total_pokemon"] == 1


def test_stats_accept_a_timezone(client: TestClient, auth_headers: dict, api, stored) -> None:
    stored(25, captured_days_ago=0)

    response = client.get(STATS, params={"tz": "Europe/Madrid"}, headers=auth_headers)

    assert response.status_code == 200
    assert response.json()["timezone"] == "Europe/Madrid"


@pytest.mark.parametrize("tz", ["Marte/Olympus", "../../etc/passwd", "", "x" * 65])
def test_invalid_timezone_is_rejected(client: TestClient, auth_headers: dict, tz: str) -> None:
    assert client.get(STATS, params={"tz": tz}, headers=auth_headers).status_code == 422


def test_stats_work_when_pokeapi_is_down(client: TestClient, auth_headers: dict, stored) -> None:
    async def no_sleep(_: float) -> None:
        return None

    client.app.state.pokeapi._sleep = no_sleep
    stored(25, captured_days_ago=0)
    with respx.mock() as router:
        router.route(host="pokeapi.co").mock(side_effect=httpx.ConnectTimeout("caída"))
        body = client.get(STATS, headers=auth_headers).json()

    assert body["captured"] == 1
    assert body["national_dex_total"] is None
    assert body["national_dex_percentage"] is None
