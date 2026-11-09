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

import re
from datetime import UTC, datetime
from typing import Any

from pydantic import field_validator, model_validator
from sqlalchemy import JSON, CheckConstraint, Column, DateTime, Dialect, UniqueConstraint
from sqlalchemy.types import TypeDecorator
from sqlmodel import Field, Relationship, SQLModel

#: Tamaño máximo de un equipo de batalla.
MAX_TEAM_SIZE = 6

#: bcrypt solo tiene en cuenta los primeros 72 *bytes* de la contraseña e ignora el
#: resto sin avisar. Se rechazan las contraseñas más largas para que no haya sorpresas.
BCRYPT_MAX_BYTES = 72
PASSWORD_MIN_LENGTH = 8

EMAIL_PATTERN = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}$")
USERNAME_PATTERN = re.compile(r"^[a-z0-9_.-]{3,50}$")


def utc_now() -> datetime:
    """Fecha y hora actual en UTC y con zona horaria (``datetime.utcnow`` está obsoleto)."""
    return datetime.now(UTC)


class UTCDateTime(TypeDecorator[datetime]):
    """Columna de fecha que siempre se guarda y se devuelve en UTC con zona horaria.

    SQLite no guarda la zona horaria y devuelve fechas *naive*; compararlas con fechas
    *aware* lanza ``TypeError``. Este tipo normaliza ambos sentidos para que el resto
    del código trabaje siempre con fechas UTC con zona horaria.
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    def process_result_value(self, value: Any, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


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
    created_at: datetime = Field(default_factory=utc_now, sa_type=UTCDateTime)

    # Protección contra fuerza bruta: tras varios fallos seguidos la cuenta se bloquea
    # temporalmente, aunque el atacante cambie de IP.
    failed_login_attempts: int = Field(default=0, ge=0)
    locked_until: datetime | None = Field(default=None, sa_type=UTCDateTime)

    pokedex_entries: list["PokedexEntry"] = Relationship(
        back_populates="owner", cascade_delete=True
    )
    teams: list["Team"] = Relationship(back_populates="trainer", cascade_delete=True)
    refresh_tokens: list["RefreshToken"] = Relationship(back_populates="user", cascade_delete=True)


class UserCreate(UserBase):
    """Datos de registro, con la política de contraseñas del enunciado."""

    password: str = Field(
        min_length=PASSWORD_MIN_LENGTH,
        max_length=BCRYPT_MAX_BYTES,
        description="Mínimo 8 caracteres, con al menos una mayúscula y un número.",
    )

    @field_validator("username")
    @classmethod
    def _normalize_username(cls, value: str) -> str:
        value = value.strip().lower()
        if not USERNAME_PATTERN.fullmatch(value):
            raise ValueError(
                "El nombre de usuario solo admite letras, números, '.', '_' y '-' "
                "(entre 3 y 50 caracteres)."
            )
        return value

    @field_validator("email")
    @classmethod
    def _validate_email(cls, value: str) -> str:
        value = value.strip().lower()
        if not EMAIL_PATTERN.fullmatch(value):
            raise ValueError("El email no tiene un formato válido.")
        return value

    @field_validator("password")
    @classmethod
    def _validate_password_policy(cls, value: str) -> str:
        if not any(char.isupper() for char in value):
            raise ValueError("La contraseña debe contener al menos una letra mayúscula.")
        if not any(char.isdigit() for char in value):
            raise ValueError("La contraseña debe contener al menos un número.")
        if len(value.encode("utf-8")) > BCRYPT_MAX_BYTES:
            raise ValueError(f"La contraseña no puede ocupar más de {BCRYPT_MAX_BYTES} bytes.")
        return value

    @model_validator(mode="after")
    def _password_must_not_contain_username(self) -> "UserCreate":
        if self.username in self.password.lower():
            raise ValueError("La contraseña no puede contener el nombre de usuario.")
        return self


class UserRead(UserBase):
    id: int
    is_active: bool
    created_at: datetime


class OAuthAccount(SQLModel, table=True):
    """Identidad de un proveedor externo (Google) vinculada a un usuario.

    Está en una tabla aparte y no en columnas de ``User`` porque así:

    - un usuario podría tener varios proveedores (Google, GitHub...);
    - se identifica por ``subject``, el ID **estable** del proveedor, y no por el email,
      que puede cambiar o reutilizarse;
    - se añade a una base de datos existente sin migrar la tabla ``user``.
    """

    __table_args__ = (UniqueConstraint("provider", "subject", name="uq_oauth_provider_subject"),)

    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True, ondelete="CASCADE")
    provider: str = Field(max_length=20)
    subject: str = Field(max_length=255)
    email: str = Field(max_length=254)
    created_at: datetime = Field(default_factory=utc_now, sa_type=UTCDateTime)


# ═════════════════════════════════════════════════════════════
#  Tokens de sesión
# ═════════════════════════════════════════════════════════════
class RefreshToken(SQLModel, table=True):
    """Registro de cada refresh token emitido, para poder rotarlos y revocarlos.

    Todos los tokens que nacen de un mismo inicio de sesión comparten ``family_id``. Si
    alguien presenta un token ya usado, se asume que lo han robado y se revoca la familia
    entera: tanto el atacante como el usuario legítimo tendrán que volver a entrar.
    """

    id: int | None = Field(default=None, primary_key=True)
    jti: str = Field(max_length=64, unique=True, index=True)
    family_id: str = Field(max_length=64, index=True)
    user_id: int = Field(foreign_key="user.id", index=True, ondelete="CASCADE")
    expires_at: datetime = Field(sa_type=UTCDateTime)
    revoked_at: datetime | None = Field(default=None, sa_type=UTCDateTime)
    created_at: datetime = Field(default_factory=utc_now, sa_type=UTCDateTime)

    user: User = Relationship(back_populates="refresh_tokens")


class TokenPair(SQLModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"  # noqa: S105  (tipo de token de OAuth2, no una contraseña)
    expires_in: int = Field(description="Segundos de validez del access token.")


class RefreshRequest(SQLModel):
    refresh_token: str = Field(min_length=1, max_length=4096)


# ═════════════════════════════════════════════════════════════
#  Pokémon (datos de PokeAPI ya transformados)
# ═════════════════════════════════════════════════════════════
# PokeAPI devuelve cientos de campos por Pokémon (movimientos, juegos, objetos...). El
# cliente extrae solo lo que usa la API y lo entrega con estos modelos, de modo que el
# resto del código no depende del formato de la API externa.
class NamedResource(SQLModel):
    id: int
    name: str


class PokemonStats(SQLModel):
    hp: int
    attack: int
    defense: int
    special_attack: int
    special_defense: int
    speed: int

    @property
    def total(self) -> int:
        return (
            self.hp
            + self.attack
            + self.defense
            + self.special_attack
            + self.special_defense
            + self.speed
        )


class PokemonSprites(SQLModel):
    front_default: str | None = None
    front_shiny: str | None = None
    official_artwork: str | None = None


class PokemonAbility(SQLModel):
    name: str
    is_hidden: bool = False


class PokemonSummary(SQLModel):
    """Versión reducida para listados: ``{id, name, sprite, types}``."""

    id: int
    name: str
    sprite: str | None
    types: list[str]


class PokemonDetail(SQLModel):
    id: int
    name: str
    species_name: str | None = Field(
        default=None,
        description="Especie a la que pertenece (las formas alternativas comparten especie).",
    )
    height: int = Field(description="Altura en decímetros.")
    weight: int = Field(description="Peso en hectogramos.")
    base_experience: int | None = None
    types: list[str]
    abilities: list[PokemonAbility]
    stats: PokemonStats
    sprites: PokemonSprites

    def to_summary(self) -> PokemonSummary:
        return PokemonSummary(
            id=self.id,
            name=self.name,
            sprite=self.sprites.front_default or self.sprites.official_artwork,
            types=self.types,
        )


class PokemonSpecies(SQLModel):
    id: int
    name: str
    display_name: str = Field(description="Nombre en español si existe.")
    genus: str | None = Field(default=None, description="Categoría, p. ej. «Pokémon Ratón».")
    description: str | None = Field(default=None, description="Entrada de la Pokédex.")
    generation: str | None = None
    is_legendary: bool = False
    is_mythical: bool = False
    evolution_chain_id: int | None = None


class PokemonFullDetail(PokemonDetail):
    """Detalle del Pokémon junto con la información de su especie."""

    species: PokemonSpecies | None = Field(
        default=None,
        description="Puede faltar si PokeAPI no la devuelve; el resto sigue siendo válido.",
    )


class PokemonPage(SQLModel):
    count: int
    limit: int
    offset: int
    results: list[NamedResource]


class PokemonSearchResult(SQLModel):
    count: int
    limit: int
    offset: int
    results: list[PokemonSummary]


# ═════════════════════════════════════════════════════════════
#  Pokédex
# ═════════════════════════════════════════════════════════════
class PokedexEntryBase(SQLModel):
    is_captured: bool = False
    capture_date: datetime | None = Field(default=None, sa_type=UTCDateTime)
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

    created_at: datetime = Field(default_factory=utc_now, sa_type=UTCDateTime)

    owner: User = Relationship(back_populates="pokedex_entries")


def _clean_optional_text(value: str | None) -> str | None:
    """Quita espacios sobrantes; un texto vacío se guarda como ``None``."""
    if value is None:
        return None
    value = " ".join(value.split())
    return value or None


def _not_in_future(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    if value > utc_now():
        raise ValueError("La fecha de captura no puede estar en el futuro.")
    return value


class PokedexEntryCreate(SQLModel):
    pokemon_id: int = Field(ge=1, le=100_000, description="ID del Pokémon en PokeAPI.")
    nickname: str | None = Field(default=None, max_length=50)
    notes: str | None = Field(default=None, max_length=500)
    is_captured: bool = False
    capture_date: datetime | None = Field(
        default=None,
        description="Si se marca como capturado sin fecha, se usa la fecha actual.",
    )
    favorite: bool = False

    @field_validator("nickname", "notes")
    @classmethod
    def _clean_texts(cls, value: str | None) -> str | None:
        return _clean_optional_text(value)

    @field_validator("capture_date")
    @classmethod
    def _check_date(cls, value: datetime | None) -> datetime | None:
        return _not_in_future(value)

    @model_validator(mode="after")
    def _date_requires_capture(self) -> "PokedexEntryCreate":
        if self.capture_date is not None and not self.is_captured:
            raise ValueError("Solo un Pokémon capturado puede tener fecha de captura.")
        return self


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

    @field_validator("nickname", "notes")
    @classmethod
    def _clean_texts(cls, value: str | None) -> str | None:
        return _clean_optional_text(value)

    @field_validator("capture_date")
    @classmethod
    def _check_date(cls, value: datetime | None) -> datetime | None:
        return _not_in_future(value)


class EvolutionStep(SQLModel):
    """Una especie dentro de su cadena evolutiva."""

    id: int
    name: str
    stage: int = Field(description="1 = forma base, 2 = primera evolución...")
    evolves_from: str | None = None
    condition: str | None = Field(
        default=None, description="Cómo se llega a esta especie, p. ej. «nivel 16»."
    )


class EvolutionChain(SQLModel):
    id: int
    stages: int = Field(description="Número de etapas de la cadena.")
    steps: list[EvolutionStep] = Field(description="Todas las especies, en orden de etapa.")


class EvolutionTarget(SQLModel):
    id: int
    name: str
    condition: str | None


class EntryEvolution(SQLModel):
    """Posición de un Pokémon de la Pokédex dentro de su cadena evolutiva."""

    chain_id: int
    stage: int
    total_stages: int
    evolves_from: str | None
    evolves_to: list[EvolutionTarget]
    is_final_stage: bool


class PokedexEntryV2(PokedexEntryRead):
    evolution: EntryEvolution | None = Field(
        description="null si PokeAPI no está disponible o la especie no tiene cadena."
    )


class PokedexCursorPage(SQLModel):
    items: list[PokedexEntryV2]
    limit: int
    next_cursor: str | None = Field(
        description="Pásalo como `cursor` para pedir la página siguiente; null si no hay más."
    )


class PokedexStats(SQLModel):
    """Estadísticas de la Pokédex. Los seis primeros campos son los del enunciado."""

    total_pokemon: int = Field(description="Pokémon guardados en la Pokédex.")
    captured: int
    favorites: int
    completion_percentage: float = Field(
        description="Porcentaje de los Pokémon de la Pokédex que están capturados (1 decimal)."
    )
    most_common_type: str | None = Field(
        description="Tipo más frecuente; si hay empate, el primero alfabéticamente."
    )
    capture_streak_days: int = Field(
        description="Días consecutivos con capturas hasta hoy (sigue viva si ayer hubo captura)."
    )
    longest_streak_days: int
    type_distribution: dict[str, int] = Field(description="Pokémon por tipo, de más a menos.")
    first_capture_date: datetime | None
    last_capture_date: datetime | None
    national_dex_total: int | None = Field(
        description="Especies existentes según PokeAPI; null si PokeAPI no responde."
    )
    national_dex_percentage: float | None = Field(
        description="Porcentaje de la Pokédex Nacional capturado."
    )
    timezone: str = Field(description="Zona horaria usada para decidir qué es «un día».")


class PokedexPage(SQLModel):
    count: int = Field(description="Total de entradas que cumplen los filtros.")
    limit: int
    offset: int
    results: list[PokedexEntryRead]


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
    created_at: datetime = Field(default_factory=utc_now, sa_type=UTCDateTime)
    updated_at: datetime = Field(default_factory=utc_now, sa_type=UTCDateTime)

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


def _check_team_ids(ids: list[int] | None) -> list[int] | None:
    if ids is None:
        return None
    if any(i < 1 for i in ids):
        raise ValueError("Los IDs de Pokémon deben ser positivos.")
    if len(set(ids)) != len(ids):
        raise ValueError("Un Pokémon no puede aparecer dos veces en el mismo equipo.")
    return ids


class TeamCreate(TeamBase):
    pokemon_ids: list[int] = Field(
        min_length=1,
        max_length=MAX_TEAM_SIZE,
        description="IDs de PokeAPI (como mucho 6, sin repetir) de Pokémon que ya están en la "
        "Pokédex del usuario. El orden de la lista es la posición en el equipo.",
        schema_extra={"examples": [[1, 4, 7, 25, 133, 143]]},
    )

    @field_validator("name")
    @classmethod
    def _clean_name(cls, value: str) -> str:
        cleaned = _clean_optional_text(value)
        if not cleaned:
            raise ValueError("El nombre del equipo no puede estar vacío.")
        return cleaned

    @field_validator("description")
    @classmethod
    def _clean_description(cls, value: str | None) -> str | None:
        return _clean_optional_text(value)

    @field_validator("pokemon_ids")
    @classmethod
    def _unique_ids(cls, value: list[int]) -> list[int]:
        return _check_team_ids(value)  # type: ignore[return-value]


class TeamUpdate(SQLModel):
    """Actualización del equipo. Los campos que no se envían se conservan; si se envía
    ``pokemon_ids``, sustituye a la lista completa de miembros."""

    name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=500)
    pokemon_ids: list[int] | None = Field(default=None, min_length=1, max_length=MAX_TEAM_SIZE)

    @field_validator("name")
    @classmethod
    def _clean_name(cls, value: str | None) -> str | None:
        cleaned = _clean_optional_text(value)
        if value is not None and not cleaned:
            raise ValueError("El nombre del equipo no puede estar vacío.")
        return cleaned

    @field_validator("description")
    @classmethod
    def _clean_description(cls, value: str | None) -> str | None:
        return _clean_optional_text(value)

    @field_validator("pokemon_ids")
    @classmethod
    def _unique_ids(cls, value: list[int] | None) -> list[int] | None:
        return _check_team_ids(value)


class TeamMemberRead(SQLModel):
    position: int
    pokedex_entry_id: int
    pokemon_id: int
    pokemon_name: str
    nickname: str | None
    sprite: str | None
    types: list[str]


class TeamRead(TeamBase):
    id: int
    created_at: datetime
    updated_at: datetime
    members: list[TeamMemberRead] = []


# ── Análisis del equipo ──────────────────────────────────────
class StatBlock(SQLModel):
    hp: float
    attack: float
    defense: float
    special_attack: float
    special_defense: float
    speed: float


class TeamCombinedStats(SQLModel):
    total: StatBlock = Field(description="Suma de cada estadística base del equipo.")
    average: StatBlock = Field(description="Media por miembro.")
    base_stat_total: int = Field(description="Suma de todas las estadísticas de todos.")
    average_base_stat_total: float


class TypeMatchup(SQLModel):
    type: str = Field(description="Tipo del ataque recibido.")
    weak: int = Field(description="Miembros que reciben daño supereficaz (x2 o x4).")
    resist: int = Field(description="Miembros que reciben daño reducido (x0,5 o x0,25).")
    immune: int = Field(description="Miembros inmunes (x0).")
    multipliers: list[float] = Field(description="Multiplicador para cada miembro, en orden.")


class TeamMemberAnalysis(SQLModel):
    pokemon_id: int
    name: str
    types: list[str]
    stats: StatBlock
    base_stat_total: int


class TeamAnalysis(SQLModel):
    team_id: int
    name: str
    members: list[TeamMemberAnalysis]
    combined: TeamCombinedStats
    defensive: list[TypeMatchup] = Field(description="De más peligroso a menos.")
    shared_weaknesses: list[str] = Field(
        description="Tipos a los que es débil al menos la mitad del equipo sin compensarlo."
    )
    super_effective_against: list[str] = Field(
        description="Tipos que el equipo golpea de forma supereficaz con sus propios tipos."
    )
    not_covered: list[str]
    highlights: list[str] = Field(description="Conclusiones en español calculadas con reglas.")
