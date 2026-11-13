"""Retos entre amigos: salas en memoria con código, dos jugadores y el motor de combate.

Cada sala vive en el proceso del servidor mientras dura el combate (como mucho
``ROOM_TTL_SECONDS`` sin actividad). El servidor es el árbitro: recibe la acción de cada
jugador por WebSocket y, cuando tiene las dos, resuelve el turno con el mismo motor que
contra la máquina. Ningún navegador decide el resultado.

Con varias réplicas del servidor, las salas tendrían que compartirse (por ejemplo con
Redis); para una instancia, la memoria basta.
"""

import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Final

from app.services.battle import SEED_BITS, Battle, Fighter, Side

CODE_ALPHABET: Final = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # sin 0/O ni 1/I, que se confunden
CODE_LENGTH: Final = 6
ROOM_TTL_SECONDS: Final = 15 * 60


@dataclass
class Player:
    user_id: int
    username: str
    team_id: int
    fighters: list[dict[str, Any]]
    socket: Any = None


@dataclass
class Room:
    code: str
    host: Player
    touched: float
    guest: Player | None = None
    engine: Battle | None = None
    seed: int = 0
    pending: dict[int, dict[str, Any]] = field(default_factory=dict)
    actions: list[dict[str, Any]] = field(default_factory=list)
    finished: bool = False

    @property
    def players(self) -> list[Player]:
        return [p for p in (self.host, self.guest) if p is not None]

    def side_of(self, user_id: int) -> int | None:
        if self.host.user_id == user_id:
            return 0
        if self.guest is not None and self.guest.user_id == user_id:
            return 1
        return None

    def player(self, side: int) -> Player | None:
        return self.host if side == 0 else self.guest

    def start(self) -> None:
        """Empieza el combate cuando ya hay dos jugadores."""
        if self.guest is None:
            raise ValueError("Falta el rival.")
        self.seed = secrets.randbits(SEED_BITS)
        self.engine = Battle(
            [
                Side(self.host.username, [Fighter(**f) for f in self.host.fighters]),
                Side(self.guest.username, [Fighter(**f) for f in self.guest.fighters]),
            ],
            seed=self.seed,
        )


class RoomManager:
    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._rooms: dict[str, Room] = {}
        self._clock = clock

    def _new_code(self) -> str:
        while True:
            code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))
            if code not in self._rooms:
                return code

    def create(self, host: Player) -> Room:
        """Crea un reto. Cada entrenador tiene como mucho un reto esperando rival."""
        self.cleanup()
        for code, room in list(self._rooms.items()):
            if room.host.user_id == host.user_id and room.guest is None:
                del self._rooms[code]
        room = Room(code=self._new_code(), host=host, touched=self._clock())
        self._rooms[room.code] = room
        return room

    def get(self, code: str) -> Room | None:
        self.cleanup()
        room = self._rooms.get(code.strip().upper())
        if room is not None:
            room.touched = self._clock()
        return room

    def remove(self, code: str) -> None:
        self._rooms.pop(code, None)

    def cleanup(self) -> None:
        now = self._clock()
        for code, room in list(self._rooms.items()):
            if now - room.touched > ROOM_TTL_SECONDS:
                del self._rooms[code]

    def __len__(self) -> int:
        return len(self._rooms)
