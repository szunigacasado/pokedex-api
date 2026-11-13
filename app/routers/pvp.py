"""Combates contra un amigo en directo.

1. Uno crea un reto (``POST /pvp/rooms``) con su equipo y recibe un código.
2. Los dos abren ``/pvp/ws``. El primer mensaje autentica (``{"type": "auth", "token"}``)
   y el segundo entra en la sala (``{"type": "join", "code", "team_id"}``: el equipo solo
   lo indica quien acepta el reto).
3. Cada turno, cada uno envía su acción. Cuando están las dos, el servidor resuelve el
   turno con el motor y se lo envía a los dos, cada uno desde su lado.

Seguridad del WebSocket:
- El token va en el primer mensaje, no en la URL (no queda en los logs).
- Se comprueba el ``Origin``: una web ajena no puede abrir la conexión con tu sesión
  (*Cross-Site WebSocket Hijacking*).
- Límite de mensajes por conexión y de conexiones por usuario; acciones validadas por
  el motor y mensajes que no sean objetos JSON, rechazados.
- Tiempo máximo para autenticarse y para entrar en la sala.
- La base de datos solo se usa un instante en cada operación (autenticar, cargar el
  equipo, guardar el resultado), nunca durante toda la partida: un socket abierto no
  puede dejar a la API sin conexiones.
"""

import asyncio
import logging
import time
from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Annotated, Any
from urllib.parse import urlsplit

from fastapi import (
    APIRouter,
    HTTPException,
    Request,
    Response,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from pydantic import BaseModel, Field
from sqlmodel import Session

from app import rate_limit as rl
from app.database import get_session
from app.dependencies import CurrentUser, PokeAPIDep, SessionDep, user_from_token
from app.models import Battle as BattleRecord
from app.models import User, utc_now
from app.rate_limit import limiter
from app.routers.battles import side_rewards, team_fighters
from app.services.battle import from_side
from app.services.pvp import Player, Room, RoomManager

logger = logging.getLogger("pokedex_api.pvp")

router = APIRouter(tags=["Combates"])
rooms = RoomManager()

AUTH_TIMEOUT_SECONDS = 10
MAX_MESSAGES_PER_MINUTE = 120
MAX_SOCKETS_PER_USER = 3
open_sockets: Counter[int] = Counter()


class RoomCreate(BaseModel):
    team_id: Annotated[int, Field(ge=1)]


class RoomCreated(BaseModel):
    code: str
    expires_in: int


@router.post(
    "/pvp/rooms",
    response_model=RoomCreated,
    status_code=status.HTTP_201_CREATED,
    summary="Crear un reto para un amigo",
)
@limiter.limit(rl.EXTERNAL)
async def create_room(
    request: Request,
    response: Response,
    payload: RoomCreate,
    user: CurrentUser,
    session: SessionDep,
    pokeapi: PokeAPIDep,
) -> RoomCreated:
    """Crea un reto con tu equipo y devuelve un **código** para que tu amigo se una desde
    su cuenta. Después, los dos se conectan al WebSocket `/api/v1/pvp/ws`."""
    _, fighters = await team_fighters(session, user, payload.team_id, pokeapi)
    room = rooms.create(Player(user.id, user.username, payload.team_id, fighters))
    logger.info("Reto %s creado por %s", room.code, user.username)
    return RoomCreated(code=room.code, expires_in=15 * 60)


# ═════════════════════════════════════════════════════════════
#  WebSocket
# ═════════════════════════════════════════════════════════════
@contextmanager
def short_session(ws: WebSocket) -> Iterator[Session]:
    """Una sesión de base de datos solo para una operación. Respeta la sustitución de
    ``get_session`` que hacen los tests."""
    factory = ws.app.dependency_overrides.get(get_session, get_session)
    sessions = factory()
    try:
        yield next(sessions)
    finally:
        sessions.close()


async def receive_object(ws: WebSocket, timeout: float | None = None) -> dict[str, Any]:
    """El siguiente mensaje, que tiene que ser un objeto JSON. ``ValueError`` si no lo es."""
    message = (
        await asyncio.wait_for(ws.receive_json(), timeout=timeout)
        if timeout
        else await ws.receive_json()
    )
    if not isinstance(message, dict):
        raise ValueError("El mensaje no es un objeto JSON.")
    return message


def _same_origin(ws: WebSocket) -> bool:
    origin = ws.headers.get("origin")
    if not origin:
        return False
    return urlsplit(origin).netloc.lower() == ws.headers.get("host", "").lower()


def _state(
    room: Room, side: int, events: list[dict[str, Any]] | None = None, rewards=None
) -> dict[str, Any]:
    engine = room.engine
    if engine is None:
        raise RuntimeError("El combate aún no ha empezado.")
    if not engine.finished:
        status_ = "active"
    elif engine.draw:
        status_ = "draw"
    else:
        status_ = "won" if engine.winner == side else "lost"
    return {
        "status": status_,
        "turn": engine.turn,
        "you": engine.side_view(side),
        "rival": engine.side_view(1 - side),
        "actions": engine.actions(side) if not engine.finished else None,
        "events": from_side(events or [], side),
        "rewards": rewards,
    }


async def _send(player: Player | None, message: dict[str, Any]) -> None:
    if player is not None and player.socket is not None:
        try:
            await player.socket.send_json(message)
        except (WebSocketDisconnect, RuntimeError):  # el otro se ha ido: no tumba el combate
            player.socket = None


async def _finish(
    room: Room,
    ws: WebSocket,
    pokeapi: Any,
    events: list[dict[str, Any]],
    forfeit_side: int | None = None,
) -> None:
    """Experiencia para los dos y registro en su historial. Una sola vez por sala."""
    if room.finished:
        return
    room.finished = True
    engine = room.engine
    if engine is None:
        return
    if forfeit_side is not None and not engine.finished:
        engine.winner = 1 - forfeit_side
        events = [*events, {"type": "end", "winner": engine.winner}]
    with short_session(ws) as session:
        results = await _save_results(room, session, pokeapi, engine, forfeit_side)
    for side, rewards in results:
        await _send(room.player(side), {"type": "end", **_state(room, side, events, rewards)})
    rooms.remove(room.code)
    logger.info("Reto %s terminado (ganador: %s)", room.code, engine.winner)


async def _save_results(
    room: Room, session: Session, pokeapi: Any, engine: Any, forfeit_side: int | None
) -> list[tuple[int, list[dict[str, Any]]]]:
    """Experiencia y registro del combate para cada jugador."""
    results = []
    for side in (0, 1):
        player = room.player(side)
        rival = room.player(1 - side)
        if player is None or rival is None:
            continue
        won = engine.winner == side
        rewards = await side_rewards(session, engine, side, won, pokeapi)
        session.add(
            BattleRecord(
                trainer_id=player.user_id,
                team_id=player.team_id,
                mode="friend",
                seed=room.seed,
                setup={
                    "you": side,  # el historial lo enseña desde el lado de cada jugador
                    "sides": [
                        {"name": p.username, "cpu": None, "fighters": p.fighters}
                        for p in (room.host, room.guest)
                        if p is not None
                    ],
                },
                actions=room.actions,
                status=(
                    "forfeit"
                    if forfeit_side == side
                    else "draw"
                    if engine.draw
                    else "won"
                    if won
                    else "lost"
                ),
                opponent_name=rival.username,
                rewards=rewards,
                finished_at=utc_now(),
            )
        )
        session.commit()
        results.append((side, rewards))
    return results


@router.websocket("/pvp/ws")
async def pvp_socket(ws: WebSocket) -> None:
    if not _same_origin(ws):
        await ws.close(code=1008, reason="Origen no permitido")
        return
    await ws.accept()
    pokeapi = ws.app.state.pokeapi

    async def fail(message: str, close: bool = False) -> None:
        await ws.send_json({"type": "error", "message": message})
        if close:
            await ws.close(code=1008)

    # 1. Autenticación: el token llega en el primer mensaje, nunca en la URL
    try:
        first = await receive_object(ws, AUTH_TIMEOUT_SECONDS)
    except (TimeoutError, WebSocketDisconnect, ValueError):
        await ws.close(code=1008)
        return
    with short_session(ws) as session:
        user = user_from_token(session, first.get("token")) if first.get("type") == "auth" else None
    if user is None:
        await fail("Tu sesión no es válida. Vuelve a entrar.", close=True)
        return
    if open_sockets[user.id] >= MAX_SOCKETS_PER_USER:
        await fail("Tienes demasiados combates abiertos. Cierra alguno.", close=True)
        return
    open_sockets[user.id] += 1
    try:
        await _play(ws, user, pokeapi, fail)
    finally:
        open_sockets[user.id] -= 1
        if open_sockets[user.id] <= 0:
            del open_sockets[user.id]


async def _play(ws: WebSocket, user: User, pokeapi: Any, fail: Any) -> None:
    # 2. Entrar en la sala, también con tiempo máximo
    try:
        join = await receive_object(ws, AUTH_TIMEOUT_SECONDS)
    except (TimeoutError, WebSocketDisconnect, ValueError):
        await ws.close(code=1008)
        return
    room = rooms.get(str(join.get("code", ""))) if join.get("type") == "join" else None
    if room is None:
        await fail("No existe ningún reto con ese código (o ha caducado).", close=True)
        return
    side = room.side_of(user.id)
    if side is None:
        if room.guest is not None:
            await fail("Ese reto ya tiene dos entrenadores.", close=True)
            return
        try:
            team_id = int(join.get("team_id") or 0)
            with short_session(ws) as session:
                _, fighters = await team_fighters(session, user, team_id, pokeapi)
        except (HTTPException, ValueError, TypeError):  # equipo inexistente, ajeno o vacío
            await fail("Elige uno de tus equipos (con al menos un Pokémon).", close=True)
            return
        # Mientras se cargaba el equipo, otro rival ha podido llegar antes
        if room.guest is not None or room.finished:
            await fail("Ese reto ya tiene dos entrenadores.", close=True)
            return
        room.guest = Player(user.id, user.username, team_id, fighters)
        side = 1
    me = room.player(side)
    if me is None:
        return
    me.socket = ws

    if room.engine is None and room.guest is not None:
        room.start()
        logger.info("Reto %s: %s contra %s", room.code, room.host.username, room.guest.username)
    if room.engine is None:
        await ws.send_json({"type": "waiting", "code": room.code})
    else:
        for s in (0, 1):
            await _send(room.player(s), {"type": "start", **_state(room, s)})

    # 3. Turnos
    window_start, sent = time.monotonic(), 0
    try:
        while True:
            try:
                message = await receive_object(ws)
            except ValueError:
                await fail("Mensaje no válido.")
                continue
            if time.monotonic() - window_start > 60:
                window_start, sent = time.monotonic(), 0
            sent += 1
            if sent > MAX_MESSAGES_PER_MINUTE:
                await fail("Demasiados mensajes.", close=True)
                return
            if room.finished or room.engine is None:
                continue
            if message.get("type") == "forfeit":
                await _finish(room, ws, pokeapi, [], forfeit_side=side)
                return
            if message.get("type") != "action":
                continue
            action = message.get("action")
            if (
                not isinstance(action, dict)
                or side in room.pending
                or not room.engine.is_valid(side, action)
            ):
                await fail("Esa acción no está disponible.")
                continue
            room.pending[side] = {k: v for k, v in action.items() if k in ("type", "index", "to")}
            await ws.send_json({"type": "waiting_rival"})
            await _send(room.player(1 - side), {"type": "rival_ready"})
            if len(room.pending) == 2:
                turn, room.pending = room.pending, {}
                room.actions.append({str(k): v for k, v in turn.items()})
                events = room.engine.play_turn(turn)
                if room.engine.finished:
                    await _finish(room, ws, pokeapi, events)
                    return
                for s in (0, 1):
                    await _send(room.player(s), {"type": "turn", **_state(room, s, events)})
    except WebSocketDisconnect:
        me.socket = None
        if room.engine is not None and not room.finished:
            # Quien se va pierde: así nadie se libra de una derrota cerrando la pestaña
            await _send(room.player(1 - side), {"type": "rival_left"})
            await _finish(room, ws, pokeapi, [], forfeit_side=side)
        elif room.engine is None and side == 0:
            rooms.remove(room.code)
