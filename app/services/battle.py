"""Motor de combates por turnos.

Python puro: sin base de datos, sin red y con toda la aleatoriedad en un ``random.Random``
con semilla. Así un combate es **reproducible**: con la misma semilla, los mismos equipos y
las mismas acciones sale exactamente lo mismo. Gracias a eso el servidor no guarda el
estado en memoria: guarda la semilla, la foto inicial y la lista de acciones, y reconstruye
el combate en cada turno (ver ``app/routers/battles.py``). El mismo motor sirve contra la
máquina y contra un amigo.

Reglas (simplificadas, con las fórmulas clásicas del género):

- Estadísticas según el nivel: ``PS = 2·base·nivel/100 + nivel + 10`` y
  ``resto = 2·base·nivel/100 + 5``.
- Cada Pokémon conoce un ataque por cada uno de sus tipos y uno de tipo Normal. Usa el
  Ataque o el Ataque Especial, el que tenga más alto.
- Daño: ``((2·nivel/5 + 2) · potencia · A/D) / 50 + 2``, multiplicado por la bonificación
  de su propio tipo (×1,5), la tabla de tipos (×4 a ×0), un crítico (×1,5, 1 de cada 16) y
  una variación del 85 % al 100 %.
- Primero los cambios; después ataca el más rápido (empate: al azar). Si el que está en
  el campo se debilita, sale el siguiente de su equipo.
"""

import random
from dataclasses import dataclass, field
from typing import Any, Final, Literal

from app.services.team_analysis import effectiveness

Difficulty = Literal["easy", "normal", "hard"]

MAX_TURNS: Final = 150
CRIT_CHANCE: Final = 1 / 16
STAT_KEYS: Final = ("hp", "attack", "defense", "special_attack", "special_defense", "speed")

# Un ataque por tipo: (nombre, potencia). Nombres propios y descriptivos.
TYPE_MOVES: Final = {
    "normal": ("Embestida", 50),
    "fire": ("Llamarada ígnea", 75),
    "water": ("Torrente", 75),
    "electric": ("Descarga", 75),
    "grass": ("Látigo vegetal", 75),
    "ice": ("Ventisca helada", 75),
    "fighting": ("Golpe certero", 75),
    "poison": ("Ácido tóxico", 70),
    "ground": ("Temblor", 75),
    "flying": ("Picado aéreo", 70),
    "psychic": ("Onda mental", 75),
    "bug": ("Picadura", 65),
    "rock": ("Avalancha", 75),
    "ghost": ("Sombra fantasma", 70),
    "dragon": ("Furia dragón", 80),
    "dark": ("Mordisco oscuro", 70),
    "steel": ("Cabeza de hierro", 75),
    "fairy": ("Brillo feérico", 75),
}


@dataclass(frozen=True)
class Move:
    name: str
    type: str
    power: int
    category: Literal["physical", "special"]


def scaled_stats(base: dict[str, int], level: int) -> dict[str, int]:
    """Estadísticas reales a un nivel a partir de las base."""
    stats = {k: (2 * base[k] * level) // 100 + 5 for k in STAT_KEYS if k != "hp"}
    stats["hp"] = (2 * base["hp"] * level) // 100 + level + 10
    return stats


def moves_for(types: list[str], base: dict[str, int]) -> list[Move]:
    category: Literal["physical", "special"] = (
        "physical" if base["attack"] >= base["special_attack"] else "special"
    )
    move_types = list(dict.fromkeys([*types, "normal"]))  # sin repetir Normal
    return [Move(TYPE_MOVES[t][0], t, TYPE_MOVES[t][1], category) for t in move_types]


@dataclass
class Fighter:
    pokemon_id: int
    name: str
    types: list[str]
    level: int
    base: dict[str, int]
    nickname: str | None = None
    entry_id: int | None = None
    stats: dict[str, int] = field(init=False)
    hp: int = field(init=False)
    moves: list[Move] = field(init=False)
    took_part: bool = False

    def __post_init__(self) -> None:
        self.stats = scaled_stats(self.base, self.level)
        self.hp = self.stats["hp"]
        self.moves = moves_for(self.types, self.base)

    @property
    def max_hp(self) -> int:
        return self.stats["hp"]

    @property
    def alive(self) -> bool:
        return self.hp > 0

    def view(self, index: int) -> dict[str, Any]:
        return {
            "index": index,
            "pokemon_id": self.pokemon_id,
            "name": self.name,
            "nickname": self.nickname,
            "types": self.types,
            "level": self.level,
            "hp": self.hp,
            "max_hp": self.max_hp,
            "alive": self.alive,
        }


@dataclass
class Side:
    name: str
    fighters: list[Fighter]
    cpu: Difficulty | None = None  # None = lo controla una persona
    active: int = 0

    @property
    def current(self) -> Fighter:
        return self.fighters[self.active]

    @property
    def defeated(self) -> bool:
        return all(not f.alive for f in self.fighters)


def damage(
    attacker: Fighter, defender: Fighter, move: Move, rng: random.Random
) -> tuple[int, float, bool]:
    """Daño, multiplicador de tipo y si ha sido crítico."""
    multiplier = effectiveness(move.type, defender.types)
    if multiplier == 0:
        return 0, 0.0, False
    if move.category == "physical":
        attack, defense = attacker.stats["attack"], defender.stats["defense"]
    else:
        attack, defense = attacker.stats["special_attack"], defender.stats["special_defense"]
    base = ((2 * attacker.level / 5 + 2) * move.power * attack / max(1, defense)) / 50 + 2
    stab = 1.5 if move.type in attacker.types else 1.0
    critical = rng.random() < CRIT_CHANCE
    variation = rng.uniform(0.85, 1.0)
    total = base * stab * multiplier * (1.5 if critical else 1.0) * variation
    return max(1, int(total)), multiplier, critical


class Battle:
    """Un combate entre dos bandos. ``play_turn`` recibe la acción de cada persona; los
    bandos de la máquina eligen la suya con el mismo generador aleatorio."""

    def __init__(self, sides: list[Side], seed: int) -> None:
        self.sides = sides
        # Azar del juego, no criptográfico: tiene que ser reproducible con la semilla.
        self.rng = random.Random(seed)  # noqa: S311
        self.turn = 0
        self.winner: int | None = None
        self.draw = False
        for side in sides:
            side.current.took_part = True

    @property
    def finished(self) -> bool:
        return self.winner is not None or self.draw

    # ── Acciones posibles ───────────────────────────────────
    def actions(self, side_index: int) -> dict[str, Any]:
        side = self.sides[side_index]
        rival = self.sides[1 - side_index].current
        return {
            "moves": [
                {
                    "index": i,
                    "name": m.name,
                    "type": m.type,
                    "power": m.power,
                    "category": m.category,
                    "effectiveness": effectiveness(m.type, rival.types),
                }
                for i, m in enumerate(side.current.moves)
            ],
            "switch": [i for i, f in enumerate(side.fighters) if f.alive and i != side.active],
        }

    def is_valid(self, side_index: int, action: dict[str, Any]) -> bool:
        options = self.actions(side_index)
        if action.get("type") == "move":
            return action.get("index") in range(len(options["moves"]))
        if action.get("type") == "switch":
            return action.get("to") in options["switch"]
        return False

    # ── Inteligencia de la máquina ──────────────────────────
    def cpu_action(self, side_index: int) -> dict[str, Any]:
        side = self.sides[side_index]
        rival = self.sides[1 - side_index].current
        moves = side.current.moves
        if side.cpu == "easy" or self.rng.random() < 0.15:
            return {"type": "move", "index": self.rng.randrange(len(moves))}

        def expected(move: Move) -> float:
            stab = 1.5 if move.type in side.current.types else 1.0
            return move.power * stab * effectiveness(move.type, rival.types)

        best = max(range(len(moves)), key=lambda i: (expected(moves[i]), -i))
        # En difícil, cambia si el rival le hace mucho daño y tiene a alguien que lo resista
        if side.cpu == "hard" and any(
            effectiveness(t, side.current.types) >= 2 for t in rival.types
        ):
            for i, f in enumerate(side.fighters):
                resists = all(effectiveness(t, f.types) < 1 for t in rival.types)
                if f.alive and i != side.active and resists and self.rng.random() < 0.6:
                    return {"type": "switch", "to": i}
        return {"type": "move", "index": best}

    # ── Turno ───────────────────────────────────────────────
    def play_turn(self, human_actions: dict[int, dict[str, Any]]) -> list[dict[str, Any]]:
        """Resuelve un turno. ``human_actions`` = {índice de bando: acción}."""
        if self.finished:
            raise ValueError("El combate ya ha terminado.")
        actions: dict[int, dict[str, Any]] = {}
        for i, side in enumerate(self.sides):
            if side.cpu is not None:
                actions[i] = self.cpu_action(i)
            else:
                action = human_actions.get(i)
                if action is None or not self.is_valid(i, action):
                    raise ValueError("Acción no válida.")
                actions[i] = action

        self.turn += 1
        events: list[dict[str, Any]] = [{"type": "turn", "turn": self.turn}]

        # 1. Cambios, antes que los ataques
        for i in (0, 1):
            if actions[i]["type"] == "switch":
                self._switch_in(i, actions[i]["to"], events, reason="switch")

        # 2. Ataques por orden de velocidad
        attackers = [i for i in (0, 1) if actions[i]["type"] == "move"]
        speed = {i: self.sides[i].current.stats["speed"] for i in attackers}
        tie_break = self.rng.random()
        attackers.sort(key=lambda i: (-speed[i], i if tie_break < 0.5 else -i))
        for i in attackers:
            attacker = self.sides[i].current
            if not attacker.alive or self.finished:
                continue
            target_side = 1 - i
            defender = self.sides[target_side].current
            move = attacker.moves[actions[i]["index"]]
            events.append(
                {
                    "type": "move",
                    "side": i,
                    "fighter": self.sides[i].active,
                    "move": move.name,
                    "move_type": move.type,
                }
            )
            amount, multiplier, critical = damage(attacker, defender, move, self.rng)
            defender.hp = max(0, defender.hp - amount)
            events.append(
                {
                    "type": "damage",
                    "side": target_side,
                    "fighter": self.sides[target_side].active,
                    "amount": amount,
                    "hp": defender.hp,
                    "max_hp": defender.max_hp,
                    "effectiveness": multiplier,
                    "critical": critical,
                }
            )
            if not defender.alive:
                self._faint(target_side, events)

        if not self.finished and self.turn >= MAX_TURNS:
            self.draw = True
            events.append({"type": "end", "winner": None})
        return events

    def _switch_in(
        self, side_index: int, to: int, events: list[dict[str, Any]], reason: str
    ) -> None:
        side = self.sides[side_index]
        side.active = to
        side.current.took_part = True
        events.append(
            {"type": reason, "side": side_index, "fighter": to, "name": side.current.name}
        )

    def _faint(self, side_index: int, events: list[dict[str, Any]]) -> None:
        side = self.sides[side_index]
        events.append(
            {"type": "faint", "side": side_index, "fighter": side.active, "name": side.current.name}
        )
        if side.defeated:
            self.winner = 1 - side_index
            events.append({"type": "end", "winner": self.winner})
            return
        next_alive = next(i for i, f in enumerate(side.fighters) if f.alive)
        self._switch_in(side_index, next_alive, events, reason="send")

    # ── Vista ───────────────────────────────────────────────
    def side_view(self, side_index: int) -> dict[str, Any]:
        side = self.sides[side_index]
        return {
            "name": side.name,
            "active": side.active,
            "fighters": [f.view(i) for i, f in enumerate(side.fighters)],
        }
