"""Modelos de datos (tablas) y esquemas de entrada y salida de la API.

Se sigue el patrón *Base → Tabla / Create / Read / Update*:

- ``XBase`` reúne los campos comunes y sus validaciones.
- ``X(XBase, table=True)`` es la tabla: añade la clave primaria, las claves foráneas y
  los campos internos, que nunca se exponen (por ejemplo, ``hashed_password``).
- ``XCreate``, ``XRead`` y ``XUpdate`` definen exactamente qué entra y qué sale por
  la API, de modo que un cliente no puede fijar campos como ``owner_id`` o ``id``.

Además de las validaciones de Pydantic, las reglas críticas se refuerzan en la propia
base de datos (restricciones ``UNIQUE`` y ``CHECK``, claves foráneas con ``ON DELETE
CASCADE``), para que se cumplan aunque algún día se escriba en ella por otra vía.
"""

from datetime import UTC, datetime

from sqlalchemy import JSON, CheckConstraint, Column, UniqueConstraint
from sqlmodel import Field, Relationship, SQLModel

#: Tamaño máximo de un equipo de batalla.
MAX_TEAM_SIZE = 6


def utc_now() -> datetime:
    """Fecha y hora actual en UTC y con zona horaria (``datetime.utcnow`` está obsoleto)."""
    return datetime.now(UTC)


# ═════════════════════════════════════════════════════════════
#  Usuarios
# ═════════════════════════════════════════════════════════════
class UserBase(SQLModel):
    username: str = Field(min_length=3, max_length=50, unique=True, index=True)
    email: str = Field(max_length=254, unique=True, index=True)


class User(UserBase, table=True):
    """Usuario del sistema."""

    id: int | None = Field(default=None, primary_key=True)
    hashed_password: str
    is_active: bool = Field(default=True)
    created_at: datetime = Field(default_factory=utc_now)

    pokedex_entries: list["PokedexEntry"] = Relationship(
        back_populates="owner", cascade_delete=True
    )
    teams: list["Team"] = Relationship(back_populates="trainer", cascade_delete=True)


class UserCreate(UserBase):
    password: str = Field(min_length=8, max_length=72)


class UserRead(UserBase):
    id: int
    is_active: bool
    created_at: datetime


# ═════════════════════════════════════════════════════════════
#  Pokédex
# ═════════════════════════════════════════════════════════════
class PokedexEntryBase(SQLModel):
    is_captured: bool = False
    capture_date: datetime | None = None
    nickname: str | None = Field(default=None, max_length=50)
    notes: str | None = Field(default=None, max_length=500)
    favorite: bool = False


class PokedexEntry(PokedexEntryBase, table=True):
    """Pokémon guardado en la Pokédex de un usuario."""

    __table_args__ = (UniqueConstraint("owner_id", "pokemon_id", name="uq_pokedex_owner_pokemon"),)

    id: int | None = Field(default=None, primary_key=True)
    owner_id: int = Field(foreign_key="user.id", index=True, ondelete="CASCADE")

    # Datos copiados de PokeAPI al guardar la entrada. Así la Pokédex y sus
    # estadísticas se consultan sin depender de la disponibilidad de la API externa.
    pokemon_id: int = Field(index=True, ge=1)
    pokemon_name: str = Field(max_length=100)
    pokemon_sprite: str | None = Field(default=None, max_length=500)
    pokemon_types: list[str] = Field(default_factory=list, sa_column=Column(JSON, nullable=False))

    created_at: datetime = Field(default_factory=utc_now)

    owner: User = Relationship(back_populates="pokedex_entries")


class PokedexEntryCreate(SQLModel):
    pokemon_id: int = Field(ge=1, description="ID del Pokémon en PokeAPI.")
    nickname: str | None = Field(default=None, max_length=50)
    notes: str | None = Field(default=None, max_length=500)
    is_captured: bool = False
    favorite: bool = False


class PokedexEntryRead(PokedexEntryBase):
    id: int
    pokemon_id: int
    pokemon_name: str
    pokemon_sprite: str | None
    pokemon_types: list[str]
    created_at: datetime


class PokedexEntryUpdate(SQLModel):
    """Actualización parcial: solo se modifican los campos enviados."""

    is_captured: bool | None = None
    capture_date: datetime | None = None
    nickname: str | None = Field(default=None, max_length=50)
    notes: str | None = Field(default=None, max_length=500)
    favorite: bool | None = None


# ═════════════════════════════════════════════════════════════
#  Equipos de batalla
# ═════════════════════════════════════════════════════════════
class TeamBase(SQLModel):
    name: str = Field(min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=500)


class Team(TeamBase, table=True):
    """Equipo de batalla de un entrenador (máximo seis Pokémon)."""

    id: int | None = Field(default=None, primary_key=True)
    trainer_id: int = Field(foreign_key="user.id", index=True, ondelete="CASCADE")
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    trainer: User = Relationship(back_populates="teams")
    members: list["TeamMember"] = Relationship(
        back_populates="team",
        cascade_delete=True,
        sa_relationship_kwargs={"order_by": "TeamMember.position"},
    )


class TeamMember(SQLModel, table=True):
    """Relación muchos a muchos entre ``Team`` y ``PokedexEntry``, con su posición."""

    __table_args__ = (
        UniqueConstraint("team_id", "position", name="uq_team_member_position"),
        UniqueConstraint("team_id", "pokedex_entry_id", name="uq_team_member_entry"),
        CheckConstraint(f"position BETWEEN 1 AND {MAX_TEAM_SIZE}", name="ck_team_member_position"),
    )

    id: int | None = Field(default=None, primary_key=True)
    team_id: int = Field(foreign_key="team.id", index=True, ondelete="CASCADE")
    pokedex_entry_id: int = Field(foreign_key="pokedexentry.id", ondelete="CASCADE")
    position: int = Field(ge=1, le=MAX_TEAM_SIZE)

    team: Team = Relationship(back_populates="members")
    entry: PokedexEntry = Relationship()


class TeamCreate(TeamBase):
    pokemon_ids: list[int] = Field(
        min_length=1,
        max_length=MAX_TEAM_SIZE,
        description="IDs de PokeAPI de Pokémon que ya están en la Pokédex del usuario.",
    )


class TeamUpdate(SQLModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=500)
    pokemon_ids: list[int] | None = Field(default=None, min_length=1, max_length=MAX_TEAM_SIZE)


class TeamMemberRead(SQLModel):
    position: int
    pokedex_entry_id: int


class TeamRead(TeamBase):
    id: int
    created_at: datetime
    updated_at: datetime
    members: list[TeamMemberRead] = []
