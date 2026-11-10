// Tests de las funciones puras de la web: node --test tests/web
import assert from "node:assert/strict";
import { test } from "node:test";

import {
  artworkUrl,
  dexNumber,
  displayName,
  filenameFromDisposition,
  friendlyError,
  heightLabel,
  matchesQuery,
  normalize,
  passwordChecks,
  passwordIsValid,
  percent,
  plural,
  spriteUrl,
  typeClass,
  typeLabel,
  usernameProblem,
  validationMessage,
  weightLabel,
} from "../../app/web/js/format.js";

test("números de Pokédex con tres cifras", () => {
  assert.equal(dexNumber(1), "#001");
  assert.equal(dexNumber(25), "#025");
  assert.equal(dexNumber(1025), "#1025");
});

test("nombres de PokeAPI a nombres legibles", () => {
  assert.equal(displayName("pikachu"), "Pikachu");
  assert.equal(displayName("mr-mime"), "Mr. Mime");
  assert.equal(displayName("nidoran-f"), "Nidoran♀");
  assert.equal(displayName("tapu-koko"), "Tapu Koko");
  assert.equal(displayName(""), "");
});

test("tipos en español y con su clase de color", () => {
  assert.equal(typeLabel("electric"), "Eléctrico");
  assert.equal(typeLabel("dark"), "Siniestro");
  assert.equal(typeClass("fire"), "t-fire");
  assert.equal(typeClass("desconocido"), "t-normal");
  // Las propiedades heredadas de Object no cuentan como tipos ni nombres especiales
  assert.equal(typeClass("constructor"), "t-normal");
  assert.equal(typeLabel("constructor"), "Constructor");
  assert.equal(displayName("constructor"), "Constructor");
});

test("plurales", () => {
  assert.equal(plural(1, "segundo"), "1 segundo");
  assert.equal(plural(3, "segundo"), "3 segundos");
  assert.equal(plural(2, "Pokémon", "Pokémon"), "2 Pokémon");
});

test("la política de contraseñas coincide con la de la API", () => {
  assert.equal(passwordIsValid("Pikachu123", "ash"), true);
  const failed = (password, user) => passwordChecks(password, user).filter((c) => !c.ok).map((c) => c.id);
  assert.deepEqual(failed("corta1A", "ash"), ["length"]);
  assert.deepEqual(failed("sinmayuscula1", "ash"), ["upper"]);
  assert.deepEqual(failed("SinNumeros", "ash"), ["digit"]);
  assert.deepEqual(failed("Misty12345", "misty"), ["username"]);
  assert.deepEqual(failed(`A1${"ñ".repeat(40)}`, "ash"), ["bytes"]); // 82 bytes en UTF-8
  assert.equal(passwordChecks("", "").find((c) => c.id === "username").ok, false);
});

test("nombre de usuario", () => {
  assert.equal(usernameProblem("ash"), null);
  assert.match(usernameProblem("as"), /al menos 3/);
  assert.match(usernameProblem("ásh"), /Solo letras/);
});

test("errores de validación de la API en español", () => {
  assert.equal(validationMessage({ loc: ["body", "email"], type: "value_error", msg: "Value error, El email no es válido" }), "El email no es válido");
  assert.equal(validationMessage({ loc: ["body", "username"], type: "string_too_short", msg: "x" }), "El nombre de usuario es demasiado corto.");
  assert.equal(validationMessage({ loc: ["query", 0], type: "raro", msg: "x" }), "Un dato no es válido.");
});

test("mensajes para cada código HTTP", () => {
  assert.match(friendlyError(0), /conexión/);
  assert.equal(friendlyError(429, null, "20"), "Vas muy rápido. Espera 20 segundos y vuelve a intentarlo.");
  assert.match(friendlyError(429, null, null), /Espera un momento/);
  assert.equal(friendlyError(404, { detail: "Pokémon no encontrado: x" }), "Pokémon no encontrado: x");
  assert.match(friendlyError(401, null), /sesión/);
  assert.match(friendlyError(503, null), /problema/);
  const twoErrors = { detail: [{ loc: ["body", "email"], type: "missing" }, { loc: ["body", "email"], type: "missing" }] };
  assert.equal(friendlyError(422, twoErrors), "El email es obligatorio."); // sin repetir
});

test("nombre de fichero de Content-Disposition", () => {
  assert.equal(filenameFromDisposition('attachment; filename="pokedex-ash.csv"'), "pokedex-ash.csv");
  assert.equal(filenameFromDisposition(null, "x.pdf"), "x.pdf");
});

test("búsqueda por nombre, sin acentos ni mayúsculas, o por número", () => {
  const charizard = { id: 6, name: "charizard" };
  const mrMime = { id: 122, name: "mr-mime" };
  assert.equal(normalize("  Pokémon "), "pokemon");
  assert.ok(matchesQuery(charizard, "CHAR"));
  assert.ok(matchesQuery(charizard, "6"));
  assert.ok(matchesQuery(charizard, "#006"));
  assert.ok(!matchesQuery(charizard, "60"));
  assert.ok(matchesQuery(mrMime, "mr. mime"));
  assert.ok(matchesQuery(charizard, ""));
});

test("imágenes de PokeAPI, también la variocolor", () => {
  assert.match(spriteUrl(25), /\/sprites\/pokemon\/25\.png$/);
  assert.match(artworkUrl(25), /\/official-artwork\/25\.png$/);
  assert.match(artworkUrl(25, { shiny: true }), /\/official-artwork\/shiny\/25\.png$/);
  assert.ok(artworkUrl(1).startsWith("https://raw.githubusercontent.com/PokeAPI/"), "el único origen de imágenes que permite la CSP");
});

test("medidas de PokeAPI en metros y kilos, con coma decimal", () => {
  assert.equal(heightLabel(17), "1,7 m");
  assert.equal(weightLabel(905), "90,5 kg");
  assert.equal(percent(88.888, 1), "88,9 %");
});
