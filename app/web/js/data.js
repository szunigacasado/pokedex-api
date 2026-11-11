// Datos compartidos entre pantallas, cargados una vez y mantenidos al día:
// el catálogo de Kanto, la colección del usuario y las fichas ya consultadas.
// Menos peticiones = una web más rápida y lejos del rate limiting.

import { events, request } from "./api.js";

let kanto = null;
let collection = null;
const details = new Map();
const evolutions = new Map();
const listeners = new Set();

// Al cerrar sesión no debe quedar nada del usuario anterior.
events.addEventListener("session", (event) => {
  if (!event.detail.loggedIn) {
    collection = null;
    notify();
  }
});

function notify() {
  for (const listener of listeners) listener(collection);
}

/** Avisa cuando cambia la colección. Devuelve una función para dejar de escuchar. */
export function onCollectionChange(listener) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

// ── Catálogo de Kanto (una sola petición, datos locales del servidor) ─────────
export async function loadKanto() {
  kanto ??= await request("/api/v1/pokemon/kanto");
  return kanto;
}

export function kantoById(id) {
  return kanto?.find((p) => p.id === Number(id)) ?? null;
}

// ── Fichas completas y evoluciones (consultan PokeAPI: se guardan) ───────────
export async function loadDetail(idOrName) {
  const key = String(idOrName).toLowerCase();
  if (!details.has(key)) details.set(key, request(`/api/v1/pokemon/${encodeURIComponent(key)}`));
  try {
    return await details.get(key);
  } catch (error) {
    details.delete(key);
    throw error;
  }
}

export async function loadEvolution(id) {
  if (!evolutions.has(id)) {
    evolutions.set(id, request(`/api/v2/pokemon/${id}/evolution`).catch(() => null));
  }
  return evolutions.get(id);
}

// ── Mi colección ─────────────────────────────────────────────
export async function loadCollection({ force = false } = {}) {
  if (collection && !force) return collection;
  const all = [];
  let offset = 0;
  for (;;) {
    const page = await request("/api/v1/pokedex", { query: { limit: 100, offset } });
    all.push(...page.results);
    offset += page.limit;
    if (offset >= page.count || page.results.length === 0) break;
  }
  collection = all;
  notify();
  return collection;
}

export function entryFor(pokemonId) {
  return collection?.find((entry) => entry.pokemon_id === Number(pokemonId)) ?? null;
}

export async function addToCollection(pokemonId, extra = {}) {
  const entry = await request("/api/v1/pokedex", { method: "POST", json: { pokemon_id: Number(pokemonId), ...extra } });
  collection = [...(collection ?? []), entry];
  notify();
  return entry;
}

export async function updateEntry(entryId, changes) {
  const entry = await request(`/api/v1/pokedex/${entryId}`, { method: "PATCH", json: changes });
  collection = (collection ?? []).map((e) => (e.id === entry.id ? entry : e));
  notify();
  return entry;
}

export async function releaseEntry(entryId) {
  await request(`/api/v1/pokedex/${entryId}`, { method: "DELETE" });
  collection = (collection ?? []).filter((e) => e.id !== entryId);
  notify();
}
