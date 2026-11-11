"""Motor de combates y progresión: funciones puras, sin base de datos ni red."""

import random

import pytest

from app.models import EvolutionChain, EvolutionStep, PokemonSpecies
from app.services.battle import MAX_TURNS, Battle, Fighter, Side, damage, scaled_stats
from app.services.pokeapi_service import FallbackData
from app.services.progress import (
    MAX_LEVEL,
    apply_xp,
    battle_xp,
    fighter_setup,
    generate_rival,
    level_evolution,
    xp_for_defeat,
    xp_to_next,
)

KANTO = {p.id: p for p in FallbackData().all_pokemon()}
FLAT = {
    "hp": 80,
    "attack": 80,
    "defense": 80,
    "special_attack": 80,
    "special_defense": 80,
    "speed": 80,
}


def fighter(pokemon_id: int, level: int = 10) -> Fighter:
    return Fighter(**fighter_setup(KANTO[pokemon_id], level))


def custom(types: list[str], speed: int = 80, level: int = 10) -> Fighter:
    return Fighter(
        pokemon_id=0, name="prueba", types=types, level=level, base={**FLAT, "speed": speed}
    )


def duel(a: list[Fighter], b: list[Fighter], seed: int = 1, cpu: str | None = "normal") -> Battle:
    return Battle([Side("Tú", a), Side("Rival", b, cpu=cpu)], seed=seed)


# ── Fórmulas ─────────────────────────────────────────────────
def test_stats_grow_with_level() -> None:
    pikachu = KANTO[25].stats.model_dump()
    assert scaled_stats(pikachu, 10)["hp"] == (2 * 35 * 10) // 100 + 10 + 10
    assert scaled_stats(pikachu, 50)["speed"] > scaled_stats(pikachu, 10)["speed"]


def test_each_pokemon_has_a_move_per_type_plus_normal() -> None:
    assert [m.type for m in fighter(6).moves] == ["fire", "flying", "normal"]  # Charizard
    assert [m.type for m in fighter(143).moves] == ["normal"]  # Snorlax: sin repetir
    assert fighter(65).moves[0].category == "special"  # Alakazam: más Ataque Especial


def test_type_chart_drives_damage() -> None:
    rng = random.Random(0)
    water = custom(["water"])
    hits = {
        t: damage(water, custom([t]), water.moves[0], random.Random(0))[0]
        for t in ("fire", "normal", "grass")
    }
    assert hits["fire"] > hits["normal"] > hits["grass"]
    assert damage(custom(["normal"]), custom(["ghost"]), custom(["normal"]).moves[0], rng) == (
        0,
        0.0,
        False,
    )


def test_same_type_bonus() -> None:
    target = custom(["normal"])
    fire_user, plain_user = custom(["fire"]), custom(["water"])
    fire_move = fire_user.moves[0]
    with_bonus = damage(fire_user, target, fire_move, random.Random(3))[0]
    without = damage(plain_user, target, fire_move, random.Random(3))[0]
    assert with_bonus > without


# ── Turnos ───────────────────────────────────────────────────
def test_same_seed_and_actions_give_the_same_battle() -> None:
    def play(seed: int) -> list:
        battle = duel([fighter(4), fighter(7)], [fighter(1), fighter(25)], seed=seed)
        return [battle.play_turn({0: {"type": "move", "index": 0}}) for _ in range(4)]

    assert play(42) == play(42)
    assert play(42) != play(43)


def test_the_faster_one_attacks_first() -> None:
    battle = duel([custom(["normal"], speed=200)], [custom(["normal"], speed=10)])
    moves = [e for e in battle.play_turn({0: {"type": "move", "index": 0}}) if e["type"] == "move"]
    assert moves[0]["side"] == 0


def test_switching_happens_before_attacks() -> None:
    battle = duel([fighter(4), fighter(7)], [fighter(1, level=2)])
    events = battle.play_turn({0: {"type": "switch", "to": 1}})
    assert events[1]["type"] == "switch"
    assert battle.sides[0].active == 1
    assert battle.sides[0].fighters[1].took_part


def test_fainted_pokemon_is_replaced_and_the_battle_ends() -> None:
    battle = duel(
        [fighter(150, level=60)], [fighter(10, level=3), fighter(13, level=3)], cpu="easy"
    )
    turns = 0
    while not battle.finished and turns < 20:
        battle.play_turn({0: {"type": "move", "index": 0}})
        turns += 1
    assert battle.winner == 0
    assert battle.sides[1].defeated


def test_invalid_actions_are_rejected() -> None:
    battle = duel([fighter(4), fighter(7)], [fighter(1)])
    for action in ({"type": "switch", "to": 0}, {"type": "move", "index": 9}, {"type": "dance"}):
        assert not battle.is_valid(0, action)
        with pytest.raises(ValueError):
            battle.play_turn({0: action})


def test_no_turns_after_the_end() -> None:
    battle = duel([fighter(150, level=80)], [fighter(10, level=2)])
    while not battle.finished:
        battle.play_turn({0: {"type": "move", "index": 0}})
    with pytest.raises(ValueError):
        battle.play_turn({0: {"type": "move", "index": 0}})


def test_mutual_immunity_ends_in_a_draw() -> None:
    battle = duel([custom(["ghost"])], [custom(["normal"])])
    while not battle.finished:
        battle.play_turn({0: {"type": "move", "index": 0}})  # Fantasma no afecta a Normal
    assert battle.draw and battle.turn == MAX_TURNS


def test_cpu_always_chooses_valid_actions() -> None:
    for difficulty in ("easy", "normal", "hard"):
        battle = duel(
            [fighter(7), fighter(25)], [fighter(4), fighter(1), fighter(74)], cpu=difficulty, seed=7
        )
        for _ in range(10):
            if battle.finished:
                break
            assert battle.is_valid(1, battle.cpu_action(1))
            battle.play_turn({0: {"type": "move", "index": 0}})


def test_normal_cpu_picks_the_most_effective_move() -> None:
    battle = Battle([Side("Tú", [fighter(4)]), Side("Rival", [fighter(7)], cpu="normal")], seed=0)
    picks = {battle.cpu_action(1)["index"] for _ in range(30)}
    assert 0 in picks  # Torrente (Agua) contra Charmander (Fuego) es su mejor opción


def test_actions_show_effectiveness_against_the_rival() -> None:
    battle = duel([fighter(7)], [fighter(4)])
    water = next(m for m in battle.actions(0)["moves"] if m["type"] == "water")
    assert water["effectiveness"] == 2


# ── Progresión ───────────────────────────────────────────────
def test_xp_curve() -> None:
    assert xp_to_next(10) == 48
    assert xp_for_defeat(10) == 30
    assert battle_xp([10, 10], won=True) == 90
    assert battle_xp([10, 10], won=False) == 60


def test_apply_xp_levels_up_and_caps_at_100() -> None:
    result = apply_xp(10, 40, 30)  # 70 ≥ 48 → nivel 11 con 22
    assert (result.level, result.xp, result.levels_gained) == (11, 22, 1)
    assert apply_xp(10, 0, 10_000).levels_gained > 5
    assert apply_xp(MAX_LEVEL - 1, 0, 10**9).level == MAX_LEVEL


def test_rival_matches_size_level_and_theme() -> None:
    legendary = {144, 145, 146, 150}
    rival = generate_rival(
        random.Random(1),
        list(KANTO.values()),
        legendary,
        size=4,
        average_level=20,
        difficulty="hard",
        theme="water",
    )
    assert len(rival["fighters"]) == 4
    assert all("water" in f["types"] for f in rival["fighters"])
    assert all(22 <= f["level"] <= 24 for f in rival["fighters"])
    assert not {f["pokemon_id"] for f in rival["fighters"]} & legendary
    assert rival["cpu"] == "hard"


def test_rival_is_reproducible() -> None:
    args = dict(size=3, average_level=10, difficulty="normal", theme=None)
    a = generate_rival(random.Random(5), list(KANTO.values()), set(), **args)
    b = generate_rival(random.Random(5), list(KANTO.values()), set(), **args)
    assert a == b


# ── Evolución por nivel (con una PokeAPI falsa) ─────────────
class FakePokeAPI:
    def __init__(self, chain_steps, fail: bool = False) -> None:
        self.steps, self.fail = chain_steps, fail

    async def get_species(self, pokemon_id):
        from app.services.pokeapi_service import PokeAPIUnavailableError

        if self.fail:
            raise PokeAPIUnavailableError("caída")
        names = {4: "charmander", 37: "vulpix", 133: "eevee"}
        return PokemonSpecies(
            id=pokemon_id, name=names[pokemon_id], display_name="x", evolution_chain_id=1
        )

    async def get_evolution_chain(self, chain_id):
        return EvolutionChain(id=chain_id, stages=3, steps=self.steps)


CHARMANDER_CHAIN = [
    EvolutionStep(id=4, name="charmander", stage=1),
    EvolutionStep(
        id=5, name="charmeleon", stage=2, evolves_from="charmander", condition="nivel 16"
    ),
    EvolutionStep(id=6, name="charizard", stage=3, evolves_from="charmeleon", condition="nivel 36"),
]


@pytest.mark.asyncio
async def test_level_evolution_reads_the_chain() -> None:
    assert await level_evolution(FakePokeAPI(CHARMANDER_CHAIN), 4) == (5, "charmeleon", 16)


@pytest.mark.asyncio
async def test_evolutions_that_are_not_by_level_are_ignored() -> None:
    vulpix = [
        EvolutionStep(id=37, name="vulpix", stage=1),
        EvolutionStep(
            id=38, name="ninetales", stage=2, evolves_from="vulpix", condition="usar Piedra Fuego"
        ),
    ]
    assert await level_evolution(FakePokeAPI(vulpix), 37) is None
    assert await level_evolution(FakePokeAPI(CHARMANDER_CHAIN, fail=True), 4) is None
