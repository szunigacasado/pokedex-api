// Tests de la lógica de «Adivina la silueta»: node --test tests/web
import assert from "node:assert/strict";
import { test } from "node:test";

import { OPTIONS, ROUNDS, newRound, seededRandom, shuffle, verdict } from "../../app/web/js/game.js";

const CATALOG = [
  { id: 1, name: "bulbasaur", types: ["grass", "poison"] },
  { id: 4, name: "charmander", types: ["fire"] },
  { id: 6, name: "charizard", types: ["fire", "flying"] },
  { id: 7, name: "squirtle", types: ["water"] },
  { id: 25, name: "pikachu", types: ["electric"] },
  { id: 37, name: "vulpix", types: ["fire"] },
  { id: 58, name: "growlithe", types: ["fire"] },
  { id: 143, name: "snorlax", types: ["normal"] },
];

test("cada ronda tiene la respuesta y tres señuelos distintos", () => {
  const random = seededRandom(42);
  for (let i = 0; i < 50; i += 1) {
    const { answer, options } = newRound(CATALOG, { random });
    assert.equal(options.length, OPTIONS);
    assert.equal(new Set(options.map((o) => o.id)).size, OPTIONS);
    assert.ok(options.includes(answer));
  }
});

test("los señuelos se parecen a la respuesta cuando es posible", () => {
  const charmander = CATALOG[1];
  const { options } = newRound(CATALOG, { random: seededRandom(7), used: new Set(CATALOG.filter((p) => p !== charmander).map((p) => p.id)) });
  const fireDecoys = options.filter((p) => p !== charmander && p.types.includes("fire"));
  assert.ok(fireDecoys.length >= 2, "al menos dos señuelos de tipo Fuego");
});

test("una partida no repite respuesta", () => {
  const random = seededRandom(1);
  const used = new Set();
  for (let i = 0; i < CATALOG.length; i += 1) {
    const { answer } = newRound(CATALOG, { random, used });
    assert.ok(!used.has(answer.id));
    used.add(answer.id);
  }
});

test("con la misma semilla sale la misma partida", () => {
  assert.deepEqual(newRound(CATALOG, { random: seededRandom(9) }), newRound(CATALOG, { random: seededRandom(9) }));
});

test("barajar no pierde ni duplica elementos", () => {
  assert.deepEqual(shuffle([1, 2, 3, 4, 5], seededRandom(3)).sort(), [1, 2, 3, 4, 5]);
});

test("frase final según la puntuación", () => {
  assert.match(verdict(ROUNDS), /Perfecto/);
  assert.match(verdict(8), /Muy bien/);
  assert.match(verdict(5), /No está mal/);
  assert.match(verdict(1), /Ánimo/);
});
