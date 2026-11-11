// Tests de las misiones de entrenador: node --test tests/web
import assert from "node:assert/strict";
import { test } from "node:test";

import { MISSIONS, evaluateMissions } from "../../app/web/js/missions.js";

const done = (state) => evaluateMissions(state).filter((m) => m.done).map((m) => m.id);
const entry = (pokemon_id, extra = {}) => ({ pokemon_id, is_captured: false, favorite: false, nickname: null, ...extra });

test("quien empieza no tiene ninguna misión hecha", () => {
  assert.deepEqual(done({}), []);
  assert.equal(evaluateMissions({}).length, MISSIONS.length);
});

test("las misiones de la colección se deducen de los datos", () => {
  const collection = [entry(25, { is_captured: true, favorite: true }), entry(6, { nickname: "Draco" })];
  assert.deepEqual(done({ collection }), ["add", "capture", "favorite", "nickname"]);
});

test("diez capturas de Kanto; las de otras regiones no cuentan", () => {
  const kanto = Array.from({ length: 9 }, (_, i) => entry(i + 1, { is_captured: true }));
  assert.ok(!done({ collection: [...kanto, entry(152, { is_captured: true })] }).includes("ten"));
  assert.ok(done({ collection: [...kanto, entry(10, { is_captured: true })] }).includes("ten"));
});

test("las misiones de acción usan las marcas, y el equipo también cuenta si ya existe", () => {
  assert.deepEqual(done({ flags: { advice: true, ask: true, shiny: true } }), ["advice", "ask", "shiny"]);
  assert.ok(done({ teams: 2 }).includes("team"));
  assert.ok(done({ flags: { team: true } }).includes("team"));
});

test("cada misión dice adónde ir para cumplirla", () => {
  for (const mission of evaluateMissions({})) {
    assert.match(mission.href, /^#\//);
    assert.ok(mission.title && mission.hint && mission.icon);
  }
});
