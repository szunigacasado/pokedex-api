"""IDs enormes: responden 422 y no un 500 de la base de datos.

SQLite no admite enteros de más de 64 bits (OverflowError) y PostgreSQL usa columnas de
32 bits, así que todos los IDs se limitan a 2^31 - 1.
"""

import pytest
from fastapi.testclient import TestClient

HUGE = 99_999_999_999_999_999_999


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("GET", f"/api/v1/pokedex/{HUGE}", None),
        ("PATCH", f"/api/v1/pokedex/{HUGE}", {"favorite": True}),
        ("DELETE", f"/api/v1/pokedex/{HUGE}", None),
        ("GET", f"/api/v1/pokedex?offset={HUGE}", None),
        ("POST", f"/api/v1/pokedex/{HUGE}/evolve", None),
        ("GET", f"/api/v1/teams/{HUGE}", None),
        ("GET", f"/api/v1/teams/{HUGE}/export", None),
        ("GET", f"/api/v1/teams/{HUGE}/review", None),
        ("GET", f"/api/v1/battles/{HUGE}", None),
        ("POST", "/api/v1/battles", {"team_id": HUGE}),
        ("POST", "/api/v1/pvp/rooms", {"team_id": HUGE}),
        ("POST", "/api/v1/teams", {"name": "Grande", "pokemon_ids": [HUGE]}),
        (
            "POST",
            "/api/v1/oak/chat",
            {"messages": [{"role": "user", "content": "hola"}], "context": {"team_id": HUGE}},
        ),
    ],
)
def test_huge_ids_are_rejected_with_422(
    client: TestClient, auth_headers: dict, method: str, path: str, body: dict | None
) -> None:
    response = client.request(method, path, json=body, headers=auth_headers)
    assert response.status_code == 422, response.text


def test_the_largest_valid_id_is_just_not_found(client: TestClient, auth_headers: dict) -> None:
    assert client.get(f"/api/v1/pokedex/{2**31 - 1}", headers=auth_headers).status_code == 404
