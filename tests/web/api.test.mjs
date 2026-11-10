// Tests del cliente de la API de la web con un fetch simulado: node --test tests/web
import assert from "node:assert/strict";
import { beforeEach, test } from "node:test";

// Entorno mínimo de navegador
const store = new Map();
globalThis.window = {
  location: { origin: "http://pokedex.test", hash: "", pathname: "/" },
  history: { replaceState: (_, __, url) => { globalThis.window.location.hash = url.slice(url.indexOf("#")); } },
  sessionStorage: {
    getItem: (k) => (store.has(k) ? store.get(k) : null),
    setItem: (k, v) => store.set(k, String(v)),
    removeItem: (k) => store.delete(k),
  },
};

const api = await import("../../app/web/js/api.js");

let calls = [];
let replies = [];

function reply(status, body = {}, headers = {}) {
  return new Response(status === 204 ? null : JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json", ...headers },
  });
}

globalThis.fetch = async (url, init) => {
  calls.push({ url: String(url), ...init, auth: init.headers?.Authorization });
  const next = replies.shift();
  if (!next) throw new Error(`petición inesperada: ${url}`);
  return typeof next === "function" ? next(url, init) : next;
};

const PAIR = { access_token: "acc-1", refresh_token: "ref-1", expires_in: 86400, token_type: "bearer" };

beforeEach(() => {
  calls = [];
  replies = [];
  store.clear();
  api.clearSession();
});

test("el login guarda el access token en memoria y el refresh en sessionStorage", async () => {
  replies.push(reply(200, PAIR));
  await api.login("ash", "Pikachu123");

  assert.equal(api.isLoggedIn(), true);
  assert.equal(store.get("pokedex.refresh"), "ref-1");
  assert.ok(![...store.values()].includes("acc-1"), "el access token no se guarda en ningún almacén");
  assert.equal(calls[0].body.toString(), "username=ash&password=Pikachu123");
});

test("las peticiones autenticadas llevan el token Bearer", async () => {
  api.setSession(PAIR);
  replies.push(reply(200, { username: "ash" }));

  const me = await api.request("/api/v1/auth/me");

  assert.equal(me.username, "ash");
  assert.equal(calls[0].auth, "Bearer acc-1");
});

test("ante un 401 renueva la sesión una vez y repite la petición", async () => {
  api.setSession(PAIR);
  replies.push(
    reply(401, { detail: "caducado" }),
    reply(200, { ...PAIR, access_token: "acc-2", refresh_token: "ref-2" }),
    reply(200, { ok: true }),
  );

  const result = await api.request("/api/v1/pokedex");

  assert.deepEqual(result, { ok: true });
  assert.equal(calls[1].url, "http://pokedex.test/api/v1/auth/refresh");
  assert.equal(JSON.parse(calls[1].body).refresh_token, "ref-1");
  assert.equal(calls[2].auth, "Bearer acc-2");
  assert.equal(store.get("pokedex.refresh"), "ref-2"); // rotado
});

test("si no se puede renovar, cierra la sesión", async () => {
  api.setSession(PAIR);
  replies.push(reply(401, {}), reply(401, { detail: "revocado" }));
  let loggedOut = false;
  api.events.addEventListener("session", (e) => { if (!e.detail.loggedIn) loggedOut = true; }, { once: true });

  await assert.rejects(api.request("/api/v1/pokedex"), { status: 401 });
  assert.equal(api.isLoggedIn(), false);
  assert.equal(store.has("pokedex.refresh"), false);
  assert.equal(loggedOut, true);
});

test("varias peticiones con 401 comparten una sola renovación", async () => {
  api.setSession(PAIR);
  let refreshes = 0;
  const handler = (url) => {
    if (String(url).endsWith("/auth/refresh")) {
      refreshes += 1;
      return reply(200, { ...PAIR, access_token: "acc-2", refresh_token: "ref-2" });
    }
    return calls.filter((c) => c.url === String(url)).length === 1 ? reply(401, {}) : reply(200, { url: String(url) });
  };
  replies.push(...Array(6).fill(handler));

  await Promise.all([api.request("/api/v1/a"), api.request("/api/v1/b")]);

  assert.equal(refreshes, 1, "reutilizar el refresh token se consideraría un robo");
});

test("un 429 trae el mensaje y los segundos de espera", async () => {
  replies.push(reply(429, { detail: "x" }, { "retry-after": "12" }));

  await assert.rejects(api.request("/api/v1/x", { auth: false }), (error) => {
    assert.equal(error.status, 429);
    assert.equal(error.retryAfter, 12);
    assert.match(error.message, /12 segundos/);
    return true;
  });
});

test("cada petición se anuncia para el modo técnico", async () => {
  const seen = [];
  api.events.addEventListener("request", (e) => seen.push(e.detail));
  replies.push(reply(200, {}, { "x-request-id": "abc123", "x-ratelimit-remaining": "99", "x-ratelimit-limit": "100" }));

  await api.request("/api/v1/x", { auth: false, query: { q: "pika", vacio: "" } });

  const entry = seen.at(-1);
  assert.equal(entry.method, "GET");
  assert.equal(entry.path, "/api/v1/x?q=pika");
  assert.equal(entry.status, 200);
  assert.equal(entry.requestId, "abc123");
  assert.equal(entry.remaining, "99");
});

test("sin red, un error claro", async () => {
  replies.push(() => { throw new TypeError("Failed to fetch"); });

  await assert.rejects(api.request("/api/v1/x", { auth: false }), { status: 0, message: /conexión/ });
});

test("los tokens que devuelve Google se recogen y desaparecen de la URL", () => {
  window.location.hash = "#access_token=acc-g&refresh_token=ref-g&expires_in=60&token_type=bearer";

  assert.equal(api.takeTokensFromUrl(), true);
  assert.equal(api.isLoggedIn(), true);
  assert.equal(store.get("pokedex.refresh"), "ref-g");
  assert.equal(window.location.hash, "#/");
});

test("un fragmento normal no se toma por tokens", () => {
  window.location.hash = "#/buscar";
  assert.equal(api.takeTokensFromUrl(), false);
});
