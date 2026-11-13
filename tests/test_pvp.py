"""Combates contra un amigo por WebSocket.

Se prueban contra un servidor uvicorn de verdad en un hilo, con clientes WebSocket reales:
el TestClient de Starlette abre cada WebSocket en su propio bucle de eventos, y aquí los
dos jugadores se hablan entre sí, como en producción, dentro del mismo bucle.
"""

import asyncio
import inspect
import json
import threading
import time
from collections import Counter
from collections.abc import Iterator

import pytest
import uvicorn
from fastapi.testclient import TestClient
from sqlmodel import Session, select
from websockets.exceptions import ConnectionClosed
from websockets.sync.client import connect as ws_connect

from app.models import Battle, PokedexEntry
from app.routers import battles as battles_router
from app.routers import pvp as pvp_router
from app.server import bind_socket
from app.services import pvp as pvp_service


class Socket:
    """Cliente WebSocket síncrono con JSON. Se conecta al entrar en el ``with``."""

    def __init__(self, url: str, origin: str | None) -> None:
        self._url = url
        self._headers = {"Origin": origin} if origin else {}
        self._context = None
        self._ws = None

    def __enter__(self) -> "Socket":
        self._context = ws_connect(self._url, additional_headers=self._headers, open_timeout=10)
        self._ws = self._context.__enter__()
        return self

    def __exit__(self, *exc) -> None:
        self._context.__exit__(*exc)

    def send_json(self, data: dict) -> None:
        self._ws.send(json.dumps(data))

    def receive_json(self) -> dict:
        return json.loads(self._ws.recv(timeout=10))


@pytest.fixture
def live(client: TestClient) -> Iterator[str]:
    """La misma aplicación (y la misma base de datos de test) servida por uvicorn."""
    sock = bind_socket("127.0.0.1", 0)
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(client.app, lifespan="off", log_level="warning"))
    thread = threading.Thread(target=asyncio.run, args=(server.serve(sockets=[sock]),), daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        assert time.monotonic() < deadline
        time.sleep(0.02)
    yield f"127.0.0.1:{port}"
    server.should_exit = True
    thread.join(timeout=10)


@pytest.fixture
def ws(live: str):
    """Abre un WebSocket del reto: ws("http://origen") o ws(None) sin Origin."""

    def open_socket(origin: str | None = "same") -> Socket:
        return Socket(
            f"ws://{live}/api/v1/pvp/ws", f"http://{live}" if origin == "same" else origin
        )

    return open_socket


@pytest.fixture(autouse=True)
def clean_rooms(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pvp_router, "rooms", pvp_service.RoomManager())
    monkeypatch.setattr(pvp_router, "open_sockets", Counter())
    monkeypatch.setattr(pvp_service.secrets, "randbits", lambda _bits: 20261111)

    async def no_evolution(*_):
        return None

    monkeypatch.setattr(battles_router, "level_evolution", no_evolution)


@pytest.fixture
def gary(client: TestClient, session: Session) -> dict:
    """Segundo entrenador con su propio equipo de fuego."""
    client.post(
        "/api/v1/auth/register",
        json={"username": "gary", "email": "gary@kanto.dev", "password": "Eevee1234"},
    )
    tokens = client.post(
        "/api/v1/auth/login", data={"username": "gary", "password": "Eevee1234"}
    ).json()
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    user_id = client.get("/api/v1/auth/me", headers=headers).json()["id"]
    for pid, name in ((4, "charmander"), (37, "vulpix"), (58, "growlithe")):
        session.add(
            PokedexEntry(
                owner_id=user_id, pokemon_id=pid, pokemon_name=name, pokemon_types=["fire"]
            )
        )
    session.commit()
    team = client.post(
        "/api/v1/teams", json={"name": "Rival", "pokemon_ids": [58, 37, 4]}, headers=headers
    )
    assert team.status_code == 201, team.text
    return {
        "headers": headers,
        "token": tokens["access_token"],
        "team_id": team.json()["id"],
        "id": user_id,
    }


def receive_until(ws, *types: str) -> dict:
    for _ in range(50):
        message = ws.receive_json()
        if message["type"] in types:
            return message
    raise AssertionError(f"no llegó {types}")


def open_room(client: TestClient, auth_headers: dict, team_id: int) -> str:
    response = client.post("/api/v1/pvp/rooms", json={"team_id": team_id}, headers=auth_headers)
    assert response.status_code == 201, response.text
    return response.json()["code"]


def connect(ws, token: str, code: str, team_id: int | None = None) -> None:
    ws.send_json({"type": "auth", "token": token})
    ws.send_json({"type": "join", "code": code, "team_id": team_id})


def test_full_friendly_battle(
    ws,
    client: TestClient,
    auth_headers: dict,
    tokens: dict,
    session: Session,
    api,
    fire_team: int,
    gary: dict,
) -> None:
    code = open_room(client, auth_headers, fire_team)
    assert len(code) == 6 and not set(code) & set("0O1I")

    with (
        ws() as host,
        ws() as guest,
    ):
        connect(host, tokens["access_token"], code)
        assert receive_until(host, "waiting")["code"] == code
        connect(guest, gary["token"], code, gary["team_id"])
        start_host, start_guest = receive_until(host, "start"), receive_until(guest, "start")
        assert start_host["you"]["name"] == "ash" and start_host["rival"]["name"] == "gary"
        assert start_guest["you"]["name"] == "gary" and start_guest["rival"]["name"] == "ash"

        end = {}
        for _ in range(200):
            host.send_json({"type": "action", "action": {"type": "move", "index": 0}})
            receive_until(host, "waiting_rival")
            guest.send_json({"type": "action", "action": {"type": "move", "index": 0}})
            message_host = receive_until(host, "turn", "end")
            message_guest = receive_until(guest, "turn", "end")
            if message_host["type"] == "end":
                end = {"host": message_host, "guest": message_guest}
                break

    assert end, "el combate no terminó"
    statuses = {end["host"]["status"], end["guest"]["status"]}
    assert statuses == {"won", "lost"} or statuses == {"draw"}
    winner = end["host"] if end["host"]["status"] == "won" else end["guest"]
    assert winner["events"][-1] == {"type": "end", "winner": 0}  # cada uno lo ve desde su lado
    assert winner["rewards"] and all(r["xp_gained"] > 0 for r in winner["rewards"])

    records = session.exec(select(Battle).where(Battle.mode == "friend")).all()
    assert len(records) == 2
    mine = next(r for r in records if r.opponent_name == "gary")
    replay = client.get(f"/api/v1/battles/{mine.id}", headers=auth_headers).json()
    assert replay["status"] == end["host"]["status"]
    assert replay["you"]["name"] == "ash"


def test_forfeit_gives_the_win_to_the_rival(
    ws, client: TestClient, auth_headers: dict, tokens: dict, api, fire_team: int, gary: dict
) -> None:
    code = open_room(client, auth_headers, fire_team)
    with (
        ws() as host,
        ws() as guest,
    ):
        connect(host, tokens["access_token"], code)
        connect(guest, gary["token"], code, gary["team_id"])
        receive_until(host, "start"), receive_until(guest, "start")

        guest.send_json({"type": "forfeit"})

        assert receive_until(host, "end")["status"] == "won"
        assert receive_until(guest, "end")["status"] == "lost"


def test_leaving_the_battle_loses_it(
    ws, client: TestClient, auth_headers: dict, tokens: dict, api, fire_team: int, gary: dict
) -> None:
    code = open_room(client, auth_headers, fire_team)
    with ws() as host:
        connect(host, tokens["access_token"], code)
        with ws() as guest:
            connect(guest, gary["token"], code, gary["team_id"])
            receive_until(host, "start")
        # el invitado ha cerrado la pestaña
        assert receive_until(host, "rival_left")
        assert receive_until(host, "end")["status"] == "won"


def test_other_sites_cannot_open_the_socket(ws) -> None:
    """Cross-Site WebSocket Hijacking: sin Origin del propio sitio, se cierra al instante."""
    for origin in (None, "https://evil.example"):
        with pytest.raises((ConnectionClosed, OSError, Exception)), ws(origin) as sock:
            sock.receive_json()


def test_invalid_token_is_rejected(ws) -> None:
    with ws() as sock:
        sock.send_json({"type": "auth", "token": "no-es-un-token"})
        assert sock.receive_json()["type"] == "error"
        with pytest.raises(ConnectionClosed):
            sock.receive_json()


def test_unknown_code(ws, tokens: dict) -> None:
    with ws() as sock:
        connect(sock, tokens["access_token"], "ZZZZZZ")
        assert "No existe" in sock.receive_json()["message"]


def test_a_full_room_rejects_a_third_trainer(
    ws, client: TestClient, auth_headers: dict, tokens: dict, api, fire_team: int, gary: dict
) -> None:
    code = open_room(client, auth_headers, fire_team)
    client.post(
        "/api/v1/auth/register",
        json={"username": "brock", "email": "brock@kanto.dev", "password": "Onix12345"},
    )
    brock = client.post(
        "/api/v1/auth/login", data={"username": "brock", "password": "Onix12345"}
    ).json()
    with (
        ws() as host,
        ws() as guest,
    ):
        connect(host, tokens["access_token"], code)
        connect(guest, gary["token"], code, gary["team_id"])
        receive_until(host, "start")
        with ws() as third:
            connect(third, brock["access_token"], code, 1)
            assert "dos entrenadores" in third.receive_json()["message"]


def test_guest_must_bring_their_own_team(
    ws, client: TestClient, auth_headers: dict, tokens: dict, api, fire_team: int, gary: dict
) -> None:
    code = open_room(client, auth_headers, fire_team)
    with ws() as guest:
        connect(guest, gary["token"], code, fire_team)  # el equipo de ash no es suyo
        assert "Elige uno de tus equipos" in guest.receive_json()["message"]


def test_invalid_actions_are_refused(
    ws, client: TestClient, auth_headers: dict, tokens: dict, api, fire_team: int, gary: dict
) -> None:
    code = open_room(client, auth_headers, fire_team)
    with (
        ws() as host,
        ws() as guest,
    ):
        connect(host, tokens["access_token"], code)
        connect(guest, gary["token"], code, gary["team_id"])
        receive_until(host, "start")
        host.send_json({"type": "action", "action": {"type": "switch", "to": 0}})
        assert receive_until(host, "error")["message"] == "Esa acción no está disponible."


def test_room_codes_expire() -> None:
    now = [0.0]
    manager = pvp_service.RoomManager(clock=lambda: now[0])
    room = manager.create(pvp_service.Player(1, "ash", 1, []))
    now[0] = pvp_service.ROOM_TTL_SECONDS + 1
    assert manager.get(room.code) is None
    assert len(manager) == 0


def test_one_waiting_room_per_trainer() -> None:
    manager = pvp_service.RoomManager()
    first = manager.create(pvp_service.Player(1, "ash", 1, []))
    second = manager.create(pvp_service.Player(1, "ash", 1, []))
    assert manager.get(first.code) is None and manager.get(second.code) is second


# ── Revisión de seguridad ────────────────────────────────────
def test_the_socket_does_not_hold_a_database_session() -> None:
    """Una sesión abierta durante toda la partida agotaba las conexiones de la API."""
    assert list(inspect.signature(pvp_router.pvp_socket).parameters) == ["ws"]


def test_joining_has_a_time_limit(ws, tokens: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pvp_router, "AUTH_TIMEOUT_SECONDS", 0.3)
    with ws() as sock:
        sock.send_json({"type": "auth", "token": tokens["access_token"]})
        with pytest.raises(ConnectionClosed):  # no envía el «join»: se le cierra
            sock.receive_json()
    time.sleep(0.2)
    assert sum(pvp_router.open_sockets.values()) == 0  # se libera su plaza


def test_messages_must_be_json_objects(
    ws, client: TestClient, auth_headers: dict, tokens: dict, api, fire_team: int, gary: dict
) -> None:
    with ws() as sock:
        sock.send_json([])  # en lugar del objeto de autenticación
        with pytest.raises(ConnectionClosed):
            sock.receive_json()

    code = open_room(client, auth_headers, fire_team)
    with ws() as host, ws() as guest:
        connect(host, tokens["access_token"], code)
        connect(guest, gary["token"], code, gary["team_id"])
        receive_until(host, "start")
        host.send_json(1)
        assert receive_until(host, "error")["message"] == "Mensaje no válido."
        host.send_json({"type": "action", "action": "atacar"})
        assert receive_until(host, "error")["message"] == "Esa acción no está disponible."
        # La partida sigue
        host.send_json({"type": "action", "action": {"type": "move", "index": 0}})
        assert receive_until(host, "waiting_rival")


def test_a_guest_with_a_strange_team_id_is_refused(
    ws, client: TestClient, auth_headers: dict, tokens: dict, api, fire_team: int, gary: dict
) -> None:
    code = open_room(client, auth_headers, fire_team)
    with ws() as guest:
        connect(guest, gary["token"], code, {"no": "es un número"})
        assert "Elige uno de tus equipos" in guest.receive_json()["message"]


def test_sockets_per_user_are_limited(ws, tokens: dict) -> None:
    sockets = [ws() for _ in range(pvp_router.MAX_SOCKETS_PER_USER + 1)]
    opened = [sock.__enter__() for sock in sockets]
    try:
        for sock in opened[:-1]:
            sock.send_json({"type": "auth", "token": tokens["access_token"]})
        time.sleep(0.5)  # que el servidor cuente los anteriores
        opened[-1].send_json({"type": "auth", "token": tokens["access_token"]})
        assert "demasiados combates" in opened[-1].receive_json()["message"]
    finally:
        for sock in sockets:
            sock.__exit__(None, None, None)


def test_friendly_battle_seeds_fit_in_the_database(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pvp_service.secrets, "randbits", lambda bits: 2**bits - 1)
    from app.services.pokeapi_service import FallbackData
    from app.services.progress import fighter_setup

    pikachu = [fighter_setup(next(p for p in FallbackData().all_pokemon() if p.id == 25), 10)]
    room = pvp_service.RoomManager().create(pvp_service.Player(1, "ash", 1, pikachu))
    room.guest = pvp_service.Player(2, "gary", 2, pikachu)
    room.start()
    assert room.seed <= 2**31 - 1
