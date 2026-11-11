"""Combates contra la máquina, experiencia, niveles y evoluciones.

El estado de un combate no se guarda: se guardan la semilla, la foto inicial de los
equipos y las acciones, y se reconstruye repitiéndolas con el motor (que es determinista).
Así sobrevive a un reinicio y cada combate se puede reproducir exactamente.
"""

import logging
import random
import secrets
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Path, Request, Response, status
from sqlmodel import Session, col, select

from app import rate_limit as rl
from app.dependencies import CurrentUser, PokeAPIDep, SessionDep
from app.models import (
    Battle,
    BattleAction,
    BattleCreate,
    BattleReward,
    BattleSummary,
    BattleView,
    PokedexEntry,
    PokedexEntryRead,
    PokemonProgress,
    ProgressRead,
    User,
    utc_now,
)
from app.rate_limit import limiter
from app.routers.teams import _members_detail, _owned_team, _to_read
from app.services.battle import Battle as Engine
from app.services.battle import Fighter, Side
from app.services.progress import (
    DEFAULT_LEVEL,
    apply_xp,
    battle_xp,
    fighter_setup,
    generate_rival,
    level_evolution,
    xp_to_next,
)

logger = logging.getLogger("pokedex_api.battles")

router = APIRouter(tags=["Combates"])

BattleId = Annotated[int, Path(ge=1, description="ID del combate.")]
NOT_FOUND = "Combate no encontrado."
YOU, RIVAL = 0, 1


# ═════════════════════════════════════════════════════════════
#  Reconstrucción
# ═════════════════════════════════════════════════════════════
def build_engine(battle: Battle) -> tuple[Engine, list[dict[str, Any]]]:
    """El motor con todas las acciones repetidas, y los sucesos del último turno."""
    sides = [
        Side(
            name=side["name"],
            cpu=side.get("cpu"),
            fighters=[Fighter(**f) for f in side["fighters"]],
        )
        for side in battle.setup["sides"]
    ]
    engine = Engine(sides, seed=battle.seed)
    events: list[dict[str, Any]] = []
    for action in battle.actions:
        events = engine.play_turn({YOU: action})
    return engine, events


def _owned_battle(session: Session, user: User, battle_id: int) -> Battle:
    battle = session.exec(
        select(Battle).where(Battle.id == battle_id, Battle.trainer_id == user.id)
    ).first()
    if battle is None:  # 404 y no 403: no se revela que existe un combate ajeno
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=NOT_FOUND)
    return battle


def _progress(session: Session, entry_id: int) -> PokemonProgress:
    return session.get(PokemonProgress, entry_id) or PokemonProgress(entry_id=entry_id)


def _view(battle: Battle, engine: Engine, events: list[dict[str, Any]]) -> BattleView:
    return BattleView(
        id=battle.id,
        status=battle.status,
        turn=engine.turn,
        you=engine.side_view(YOU),
        rival=engine.side_view(RIVAL),
        actions=engine.actions(YOU) if battle.status == "active" else None,
        events=events,
        rewards=None if battle.rewards is None else [BattleReward(**r) for r in battle.rewards],
    )


# ═════════════════════════════════════════════════════════════
#  Recompensas
# ═════════════════════════════════════════════════════════════
async def _grant_rewards(
    session: Session, battle: Battle, engine: Engine, pokeapi: PokeAPIDep
) -> None:
    """Experiencia para cada Pokémon que ha salido al campo. Se aplica una sola vez."""
    if battle.rewards is not None:
        return
    won = battle.status == "won"
    defeated = [f.level for f in engine.sides[RIVAL].fighters if not f.alive]
    gained = battle_xp(defeated, won)
    rewards = []
    for fighter in engine.sides[YOU].fighters:
        if fighter.entry_id is None or not fighter.took_part:
            continue
        if session.get(PokedexEntry, fighter.entry_id) is None:
            continue  # liberado durante el combate
        progress = _progress(session, fighter.entry_id)
        before = progress.level
        result = apply_xp(progress.level, progress.xp, gained)
        progress.level, progress.xp = result.level, result.xp
        progress.battles += 1
        progress.wins += int(won)
        session.add(progress)
        evolves_to = None
        if result.levels_gained:
            evolution = await level_evolution(pokeapi, fighter.pokemon_id)
            if evolution and progress.level >= evolution[2]:
                evolves_to = evolution[1]
        rewards.append(
            {
                "entry_id": fighter.entry_id,
                "pokemon_id": fighter.pokemon_id,
                "name": fighter.name,
                "xp_gained": gained,
                "level_before": before,
                "level_after": progress.level,
                "evolves_to": evolves_to,
            }
        )
    battle.rewards = rewards
    battle.finished_at = utc_now()


def _finish_status(engine: Engine) -> str:
    if engine.draw:
        return "draw"
    return "won" if engine.winner == YOU else "lost"


# ═════════════════════════════════════════════════════════════
#  Endpoints
# ═════════════════════════════════════════════════════════════
@router.post(
    "/battles",
    response_model=BattleView,
    status_code=status.HTTP_201_CREATED,
    summary="Empezar un combate contra la máquina",
    responses={404: {"description": "El equipo no existe o no es tuyo"}},
)
@limiter.limit(rl.EXTERNAL)
async def start_battle(
    request: Request,
    response: Response,
    payload: BattleCreate,
    user: CurrentUser,
    session: SessionDep,
    pokeapi: PokeAPIDep,
) -> BattleView:
    """Tu equipo contra un rival de la máquina con tantos Pokémon como el tuyo y de un
    nivel parecido (fácil: 3 por debajo; difícil: 3 por encima). `theme` elige el tipo
    del rival (por ejemplo `water`)."""
    team = _to_read(_owned_team(session, user, payload.team_id))
    if not team.members:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail="El equipo está vacío.")
    details = await _members_detail(pokeapi, team)
    mine = []
    for member, pokemon in zip(team.members, details, strict=True):
        level = _progress(session, member.pokedex_entry_id).level
        mine.append(
            fighter_setup(
                pokemon, level, nickname=member.nickname, entry_id=member.pokedex_entry_id
            )
        )
    seed = secrets.randbits(32)
    legendary, _ = pokeapi.fallback.special_ids()
    rival = generate_rival(
        random.Random(seed ^ 0x5EED),  # noqa: S311  (azar del juego, reproducible)
        pokeapi.fallback.all_pokemon(),
        legendary,
        size=len(mine),
        average_level=round(sum(f["level"] for f in mine) / len(mine)),
        difficulty=payload.difficulty,
        theme=payload.theme,
    )
    battle = Battle(
        trainer_id=user.id,
        team_id=team.id,
        seed=seed,
        setup={"sides": [{"name": user.username, "cpu": None, "fighters": mine}, rival]},
        opponent_name=rival["name"],
    )
    session.add(battle)
    session.commit()
    session.refresh(battle)
    logger.info("Combate %s: %s contra %s", battle.id, user.username, rival["name"])
    engine, events = build_engine(battle)
    return _view(battle, engine, events)


@router.get("/battles/{battle_id}", response_model=BattleView, summary="Estado de un combate")
@limiter.limit(rl.DEFAULT)
def get_battle(
    request: Request,
    response: Response,
    battle_id: BattleId,
    user: CurrentUser,
    session: SessionDep,
) -> BattleView:
    battle = _owned_battle(session, user, battle_id)
    engine, events = build_engine(battle)
    return _view(battle, engine, events)


@router.post(
    "/battles/{battle_id}/turn",
    response_model=BattleView,
    summary="Jugar un turno",
    responses={
        409: {"description": "El combate ya ha terminado"},
        422: {"description": "Acción no válida"},
    },
)
@limiter.limit(rl.DEFAULT)
async def play_turn(
    request: Request,
    response: Response,
    battle_id: BattleId,
    action: BattleAction,
    user: CurrentUser,
    session: SessionDep,
    pokeapi: PokeAPIDep,
) -> BattleView:
    """Elige un ataque (`{"type": "move", "index": 0}`) o un cambio
    (`{"type": "switch", "to": 2}`). La máquina responde y se resuelve el turno."""
    battle = _owned_battle(session, user, battle_id)
    if battle.status != "active":
        raise HTTPException(status.HTTP_409_CONFLICT, detail="El combate ya ha terminado.")
    engine, _ = build_engine(battle)
    chosen = action.model_dump(exclude_none=True)
    if not engine.is_valid(YOU, chosen):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Esa acción no está disponible."
        )
    events = engine.play_turn({YOU: chosen})
    battle.actions = [*battle.actions, chosen]
    if engine.finished:
        battle.status = _finish_status(engine)
        await _grant_rewards(session, battle, engine, pokeapi)
    session.add(battle)
    session.commit()
    session.refresh(battle)
    return _view(battle, engine, events)


@router.post("/battles/{battle_id}/forfeit", response_model=BattleView, summary="Rendirse")
@limiter.limit(rl.DEFAULT)
def forfeit(
    request: Request,
    response: Response,
    battle_id: BattleId,
    user: CurrentUser,
    session: SessionDep,
) -> BattleView:
    """Abandona el combate. No da experiencia."""
    battle = _owned_battle(session, user, battle_id)
    if battle.status == "active":
        battle.status = "forfeit"
        battle.rewards = []
        battle.finished_at = utc_now()
        session.add(battle)
        session.commit()
        session.refresh(battle)
    engine, events = build_engine(battle)
    return _view(battle, engine, events)


@router.get("/battles", response_model=list[BattleSummary], summary="Tus últimos combates")
@limiter.limit(rl.DEFAULT)
def list_battles(
    request: Request, response: Response, user: CurrentUser, session: SessionDep
) -> list[Battle]:
    return list(
        session.exec(
            select(Battle)
            .where(Battle.trainer_id == user.id)
            .order_by(col(Battle.id).desc())
            .limit(20)
        ).all()
    )


# ═════════════════════════════════════════════════════════════
#  Niveles y evolución
# ═════════════════════════════════════════════════════════════
@router.get("/progress", response_model=list[ProgressRead], summary="Nivel de tus Pokémon")
@limiter.limit(rl.DEFAULT)
def list_progress(
    request: Request, response: Response, user: CurrentUser, session: SessionDep
) -> list[ProgressRead]:
    """Nivel y experiencia de cada Pokémon de tu Pokédex (nivel 10 si aún no ha combatido)."""
    entries = session.exec(select(PokedexEntry.id).where(PokedexEntry.owner_id == user.id)).all()
    rows = {
        p.entry_id: p
        for p in session.exec(
            select(PokemonProgress).where(col(PokemonProgress.entry_id).in_(entries))
        ).all()
    }
    result = []
    for entry_id in entries:
        p = rows.get(entry_id) or PokemonProgress(entry_id=entry_id, level=DEFAULT_LEVEL)
        result.append(
            ProgressRead(
                entry_id=entry_id,
                level=p.level,
                xp=p.xp,
                xp_to_next=xp_to_next(p.level),
                battles=p.battles,
                wins=p.wins,
            )
        )
    return result


@router.post(
    "/pokedex/{entry_id}/evolve",
    response_model=PokedexEntryRead,
    summary="Evolucionar un Pokémon",
    responses={
        404: {"description": "No existe o no es tuyo"},
        409: {"description": "Aún no puede evolucionar, o ya tienes su evolución"},
    },
)
@limiter.limit(rl.EXTERNAL)
async def evolve(
    request: Request,
    response: Response,
    entry_id: Annotated[int, Path(ge=1)],
    user: CurrentUser,
    session: SessionDep,
    pokeapi: PokeAPIDep,
) -> PokedexEntry:
    """Evoluciona a un Pokémon que ha alcanzado el nivel de su evolución (solo las que son
    por nivel). Conserva su apodo, sus notas y su nivel, y sigue en sus equipos."""
    entry = session.exec(
        select(PokedexEntry).where(PokedexEntry.id == entry_id, PokedexEntry.owner_id == user.id)
    ).first()
    if entry is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="No encontrado en tu Pokédex.")
    evolution = await level_evolution(pokeapi, entry.pokemon_id)
    if evolution is None:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="No evoluciona subiendo de nivel.")
    target_id, _, min_level = evolution
    level = _progress(session, entry.id).level
    if level < min_level:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail=f"Necesita llegar al nivel {min_level} (está en el {level}).",
        )
    duplicate = session.exec(
        select(PokedexEntry.id).where(
            PokedexEntry.owner_id == user.id, PokedexEntry.pokemon_id == target_id
        )
    ).first()
    if duplicate is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT, detail="Ya tienes su evolución en tu Pokédex."
        )
    target = await pokeapi.get_pokemon(target_id)
    previous = entry.pokemon_name
    entry.pokemon_id = target.id
    entry.pokemon_name = target.name
    entry.pokemon_sprite = target.sprites.front_default
    entry.pokemon_types = target.types
    session.add(entry)
    session.commit()
    session.refresh(entry)
    logger.info("Evolución: user_id=%s %s -> %s", user.id, previous, target.name)
    return entry
