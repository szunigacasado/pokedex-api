"""Script de datos de demostración (scripts/demo.py)."""

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.models import PokedexEntry, Team, User, UserCreate
from scripts.demo import CAPTURED, FAVORITES, PENDING, TEAMS, create_demo, main, random_password

PASSWORD = "Pikachu123"


def login(client: TestClient, username: str) -> dict[str, str]:
    response = client.post("/api/v1/auth/login", data={"username": username, "password": PASSWORD})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def test_demo_trainer_is_ready_to_show(client: TestClient, session: Session) -> None:
    result = create_demo(session, "demo", PASSWORD)
    headers = login(client, "demo")

    assert result.entries == len(CAPTURED) + len(PENDING)
    stats = client.get("/api/v1/pokedex/stats", params={"tz": "UTC"}, headers=headers).json()
    assert stats["total_pokemon"] == len(CAPTURED) + len(PENDING)
    assert stats["captured"] == len(CAPTURED)
    assert stats["favorites"] == len(FAVORITES)
    assert stats["capture_streak_days"] >= 9  # capturas en días seguidos hasta hoy
    teams = client.get("/api/v1/teams", headers=headers).json()
    assert [t["name"] for t in teams] == [name for name, _, _ in TEAMS]
    assert [len(t["members"]) for t in teams] == [len(m) for _, _, m in TEAMS]
    assert (
        client.get(f"/api/v1/teams/{teams[0]['id']}/analysis", headers=headers).status_code == 200
    )


def test_running_it_again_resets_the_trainer(session: Session) -> None:
    create_demo(session, "demo", PASSWORD)
    create_demo(session, "demo", PASSWORD)

    assert len(session.exec(select(User).where(User.username == "demo")).all()) == 1
    assert len(session.exec(select(PokedexEntry)).all()) == len(CAPTURED) + len(PENDING)
    assert len(session.exec(select(Team)).all()) == len(TEAMS)


def test_generated_password_follows_the_policy() -> None:
    for username in ("demo", "pk", "k"):
        for _ in range(20):
            UserCreate(
                username=username.ljust(3, "x"),
                email="d@kanto.dev",
                password=random_password(username.ljust(3, "x")),
            )


def test_an_invalid_password_is_explained(capsys) -> None:
    assert main(["--password", "corta"]) == 1
    assert "No se ha podido crear el usuario" in capsys.readouterr().err
