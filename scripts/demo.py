"""Datos de demostración: un entrenador con colección, capturas en días seguidos,
favoritos, apodos, notas y dos equipos, para enseñar la Pokédex sin preparar nada.

    uv run python -m scripts.demo
    uv run python -m scripts.demo --usuario profe --password "MiClave2026"

- Si el usuario ya existe, se borra y se crea de nuevo, así que siempre queda igual.
- No llama a PokeAPI: usa la copia local de los 151 de Kanto.
- No necesita el servidor en marcha: escribe en la base de datos de ``DATABASE_URL``.
- Sin ``--password`` se genera una contraseña aleatoria que se muestra al final.
"""

import argparse
import secrets
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta

from pydantic import ValidationError
from sqlmodel import Session, select

from app.auth import hash_password
from app.database import create_db_and_tables, engine
from app.models import PokedexEntry, Team, TeamMember, User, UserCreate, utc_now
from app.services.pokeapi_service import FallbackData

# Pokémon capturados: se reparten entre hoy y los 9 días anteriores (racha de 10 días)
CAPTURED = [
    1, 4, 7, 25, 6, 9, 94, 143, 150, 130, 131, 134, 135, 136, 133, 39, 52, 54, 58, 63,
    66, 74, 77, 79, 81, 92, 95, 104, 113, 115, 123, 125, 126, 127, 129, 137, 142, 147,
]  # fmt: skip
# En la Pokédex, pero aún sin capturar
PENDING = [144, 145, 146, 151, 148, 149, 2, 5, 8]
FAVORITES = {25, 6, 150, 143, 94, 151}
NICKNAMES = {25: "Chispitas", 6: "Draco", 143: "Dormilón", 7: "Burbujas", 94: "Sombra"}
NOTES = {
    25: "Mi primer compañero. Nunca sale de su Poké Ball.",
    150: "Capturado en la Cueva Celeste tras muchos intentos.",
    143: "Bloqueaba la Ruta 12. Lo desperté con la Poké Flauta.",
}
TEAMS = [
    ("Equipo Élite", "Mi equipo principal para la Liga Añil", [6, 25, 9, 94, 143, 150]),
    ("Equipo Agua", "Para los gimnasios de tipo Fuego y Roca", [7, 9, 130, 131, 134]),
]


@dataclass
class DemoResult:
    username: str
    password: str
    entries: int
    captured: int
    teams: int


def _capture_date(index: int, now: datetime) -> datetime:
    """Varias capturas por día, cada día de hoy hacia atrás hasta 9 días."""
    return now - timedelta(days=index % 10, minutes=5 + index)


def create_demo(session: Session, username: str, password: str) -> DemoResult:
    data = UserCreate(username=username, email=f"{username}@kanto.dev", password=password)
    old = session.exec(select(User).where(User.username == data.username)).first()
    if old is not None:
        session.delete(old)  # en cascada: su Pokédex, equipos y sesiones
        session.commit()

    user = User(username=data.username, email=data.email, hashed_password=hash_password(password))
    session.add(user)
    session.flush()

    pokemon = {p.id: p for p in FallbackData().all_pokemon()}
    now = utc_now()
    entries: dict[int, PokedexEntry] = {}
    for index, pokemon_id in enumerate(CAPTURED + PENDING):
        p = pokemon[pokemon_id]
        captured = pokemon_id in CAPTURED
        entry = PokedexEntry(
            owner_id=user.id,
            pokemon_id=p.id,
            pokemon_name=p.name,
            pokemon_sprite=p.sprites.front_default,
            pokemon_types=p.types,
            is_captured=captured,
            capture_date=_capture_date(index, now) if captured else None,
            favorite=pokemon_id in FAVORITES,
            nickname=NICKNAMES.get(pokemon_id),
            notes=NOTES.get(pokemon_id),
            created_at=now - timedelta(days=12, minutes=index),
        )
        session.add(entry)
        entries[pokemon_id] = entry
    session.flush()

    for name, description, members in TEAMS:
        team = Team(name=name, description=description, trainer_id=user.id)
        session.add(team)
        session.flush()
        for position, pokemon_id in enumerate(members, start=1):
            session.add(
                TeamMember(
                    team_id=team.id, pokedex_entry_id=entries[pokemon_id].id, position=position
                )
            )
    session.commit()
    return DemoResult(data.username, password, len(entries), len(CAPTURED), len(TEAMS))


def random_password(username: str = "demo") -> str:
    """Cumple la política de la API: mayúscula, número, al menos 8 caracteres y sin
    contener el nombre de usuario."""
    while True:
        password = f"Pk{secrets.token_urlsafe(9)}{secrets.randbelow(90) + 10}"
        if username.lower() not in password.lower():
            return password


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--usuario", default="demo", help="Nombre del entrenador (por defecto: demo)."
    )
    parser.add_argument("--password", help="Contraseña. Si no se indica, se genera una aleatoria.")
    args = parser.parse_args(argv)

    create_db_and_tables()
    password = args.password or random_password(args.usuario)
    try:
        with Session(engine) as session:
            result = create_demo(session, args.usuario, password)
    except ValidationError as exc:
        problems = "; ".join(err["msg"].removeprefix("Value error, ") for err in exc.errors())
        print(f"No se ha podido crear el usuario: {problems}", file=sys.stderr)
        return 1

    print(
        f"Entrenador de demo listo: {result.entries} Pokémon ({result.captured} capturados en "
        f"los últimos 10 días), {len(FAVORITES)} favoritos y {result.teams} equipos.\n"
        f"  Usuario:    {result.username}\n"
        f"  Contraseña: {result.password}\n"
        "Entra en https://localhost (o http://127.0.0.1:8000 en desarrollo)."
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
