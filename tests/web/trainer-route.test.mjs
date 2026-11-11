import assert from "node:assert/strict";
import { test } from "node:test";

import { CAPTURE_GOAL, trainerRoute } from "../../app/web/js/trainer-route.js";

const entry = (id, captured = true) => ({ pokemon_id: id, is_captured: captured });

test("quien empieza tiene el primer paso como actual y el resto bloqueado", () => {
  const route = trainerRoute();
  assert.equal(route.current.id, "starter");
  assert.deepEqual(
    route.steps.map((s) => s.state),
    ["current", "locked", "locked", "locked", "locked"],
  );
  assert.equal(route.done, 0);
});

test("capturar cuenta solo los capturados", () => {
  const route = trainerRoute({ collection: [entry(4), entry(25, false), entry(39)] });
  assert.equal(route.current.id, "capture");
  assert.equal(route.captured, 2);
  const enough = trainerRoute({ collection: [entry(4), entry(25), entry(39)].slice(0, CAPTURE_GOAL) });
  assert.equal(enough.current.id, "team");
});

test("los pasos van en orden: un equipo no cuenta si faltan capturas", () => {
  const route = trainerRoute({ collection: [entry(4)], teams: 1, flags: { advice: true } });
  assert.equal(route.current.id, "capture");
  assert.equal(route.done, 1);
});

test("la ruta termina al ganar el primer combate", () => {
  const route = trainerRoute({ collection: [entry(1), entry(4), entry(7)], teams: 1, flags: { advice: true }, wins: 1 });
  assert.equal(route.finished, true);
  assert.equal(route.current, null);
});
