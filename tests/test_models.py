"""Tests de los modelos: las reglas críticas se cumplen también en la base de datos."""

from datetime import UTC, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app.models import (
    MAX_TEAM_SIZE,
    PokedexEntry,
    Team,
    TeamCreate,
    TeamMember,
    User,
    UserCreate,
    utc_now,
)


def make_user(session: Session, username: str = "ash") -> User:
    user = User(username=username, email=f"{username}@kanto.dev", hashed_password="hash")
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


def make_entry(session: Session, owner: User, pokemon_id: int = 25) -> PokedexEntry:
    entry = PokedexEntry(
        owner_id=owner.id,
        pokemon_id=pokemon_id,
        pokemon_name=f"pokemon-{pokemon_id}",
        pokemon_types=["electric"],
    )
    session.add(entry)
    session.commit()
    session.refresh(entry)
    return entry


def test_utc_now_is_timezone_aware() -> None:
    assert utc_now().tzinfo is not None


def test_dates_are_stored_and_returned_in_utc(session: Session) -> None:
    ash = make_user(session)
    madrid_summer = timezone(timedelta(hours=2))
    ash.locked_until = datetime(2026, 7, 1, 12, 0, tzinfo=madrid_summer)
    session.add(ash)
    session.commit()
    session.expire_all()

    stored = session.get(User, ash.id).locked_until
    assert stored == datetime(2026, 7, 1, 10, 0, tzinfo=UTC)
    assert stored.tzinfo == UTC


def test_naive_dates_are_interpreted_as_utc(session: Session) -> None:
    ash = make_user(session)
    ash.locked_until = datetime(2026, 1, 1, 9, 30)  # sin zona horaria
    session.add(ash)
    session.commit()
    session.expire_all()

    assert session.get(User, ash.id).locked_until == datetime(2026, 1, 1, 9, 30, tzinfo=UTC)


def test_pokemon_types_are_stored_as_a_list(session: Session) -> None:
    entry = make_entry(session, make_user(session))
    session.expire_all()

    assert session.get(PokedexEntry, entry.id).pokemon_types == ["electric"]


def test_same_pokemon_twice_for_one_user_is_rejected(session: Session) -> None:
    ash = make_user(session)
    make_entry(session, ash, pokemon_id=25)

    with pytest.raises(IntegrityError):
        make_entry(session, ash, pokemon_id=25)


def test_same_pokemon_for_different_users_is_allowed(session: Session) -> None:
    make_entry(session, make_user(session, "ash"), pokemon_id=25)
    make_entry(session, make_user(session, "misty"), pokemon_id=25)

    assert len(session.exec(select(PokedexEntry)).all()) == 2


def test_entry_for_missing_user_is_rejected(session: Session) -> None:
    session.add(PokedexEntry(owner_id=999, pokemon_id=1, pokemon_name="bulbasaur"))

    with pytest.raises(IntegrityError):
        session.commit()


@pytest.mark.parametrize("position", [0, MAX_TEAM_SIZE + 1])
def test_team_position_outside_range_is_rejected(session: Session, position: int) -> None:
    ash = make_user(session)
    entry = make_entry(session, ash)
    team = Team(name="Elite", trainer_id=ash.id)
    session.add(team)
    session.commit()

    session.add(TeamMember(team_id=team.id, pokedex_entry_id=entry.id, position=position))
    with pytest.raises(IntegrityError):
        session.commit()


def test_team_members_are_ordered_by_position(session: Session) -> None:
    ash = make_user(session)
    pikachu, bulbasaur = make_entry(session, ash, 25), make_entry(session, ash, 1)
    team = Team(name="Elite", trainer_id=ash.id)
    session.add(team)
    session.commit()
    session.add_all(
        [
            TeamMember(team_id=team.id, pokedex_entry_id=pikachu.id, position=2),
            TeamMember(team_id=team.id, pokedex_entry_id=bulbasaur.id, position=1),
        ]
    )
    session.commit()
    session.refresh(team)

    assert [m.entry.pokemon_id for m in team.members] == [1, 25]


def test_deleting_a_user_removes_their_data(session: Session) -> None:
    ash = make_user(session)
    entry = make_entry(session, ash)
    team = Team(name="Elite", trainer_id=ash.id)
    session.add(team)
    session.commit()
    session.add(TeamMember(team_id=team.id, pokedex_entry_id=entry.id, position=1))
    session.commit()

    session.delete(ash)
    session.commit()

    for model in (PokedexEntry, Team, TeamMember):
        assert session.exec(select(model)).all() == []


def test_team_cannot_have_more_than_six_pokemon() -> None:
    with pytest.raises(ValidationError):
        TeamCreate(name="Demasiados", pokemon_ids=list(range(1, MAX_TEAM_SIZE + 2)))


def test_password_longer_than_bcrypt_limit_is_rejected() -> None:
    with pytest.raises(ValidationError):
        UserCreate(username="ash", email="ash@kanto.dev", password="A1" + "x" * 71)
