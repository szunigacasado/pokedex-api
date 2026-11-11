// Misiones de entrenador: guían a quien llega nuevo por todo lo que puede hacer.
// Unas se deducen de la colección; otras se marcan al hacer algo (pedir consejo, ver un
// variocolor…) y se recuerdan en este navegador, por usuario.

import { prefs } from "./dom.js";
import { session } from "./session.js";

/** Definición de las misiones. `check` recibe {collection, teams, flags}. */
export const MISSIONS = [
  { id: "add", icon: "plus", title: "Añade tu primer Pokémon", hint: "Abre su ficha y pulsa «Añadir a mi Pokédex».", href: "#/buscar", check: ({ collection }) => collection.length > 0 },
  { id: "capture", icon: "pokeball", title: "Captura un Pokémon", hint: "Márcalo como capturado desde su ficha o tu Pokédex.", href: "#/pokedex", check: ({ collection }) => collection.some((e) => e.is_captured) },
  { id: "favorite", icon: "star", title: "Elige un favorito", hint: "Pulsa «Marcar como favorito».", href: "#/pokedex", check: ({ collection }) => collection.some((e) => e.favorite) },
  { id: "nickname", icon: "tag", title: "Ponle un apodo", hint: "Toca un Pokémon de tu Pokédex y escribe su apodo.", href: "#/pokedex", check: ({ collection }) => collection.some((e) => e.nickname) },
  { id: "team", icon: "swords", title: "Crea tu primer equipo", hint: "Junta hasta seis Pokémon de tu colección.", href: "#/equipos/nuevo", check: ({ teams, flags }) => teams > 0 || Boolean(flags.team) },
  { id: "advice", icon: "flask", title: "Pide consejo al Profesor Oak", hint: "En la página de tu equipo.", href: "#/equipos", check: ({ flags }) => Boolean(flags.advice) },
  { id: "ask", icon: "message", title: "Hazle una pregunta a Oak", hint: "Por ejemplo: «dragones que no sean legendarios».", href: "#/oak", check: ({ flags }) => Boolean(flags.ask) },
  { id: "shiny", icon: "sparkles", title: "Descubre un variocolor", hint: "Pulsa «Variocolor» en la ficha de un Pokémon.", href: "#/pokemon/25", check: ({ flags }) => Boolean(flags.shiny) },
  { id: "guess", icon: "gamepad", title: "Adivina una silueta", hint: "Juega a «Adivina la silueta».", href: "#/juego", check: ({ flags }) => Boolean(flags.guess) },
  { id: "battle", icon: "bolt", title: "Gana tu primer combate", hint: "En «Combate», elige tu equipo y un rival.", href: "#/combate", check: ({ flags }) => Boolean(flags.battle) },
  { id: "ten", icon: "medal", title: "Captura 10 de Kanto", hint: "Ya casi eres un experto.", href: "#/buscar", check: ({ collection }) => collection.filter((e) => e.is_captured && e.pokemon_id <= 151).length >= 10 },
];

/** Estado de cada misión. Función pura: se prueba con node --test. */
export function evaluateMissions({ collection = [], teams = 0, flags = {} } = {}) {
  return MISSIONS.map(({ check, ...mission }) => ({ ...mission, done: check({ collection, teams, flags }) }));
}

function storageKey() {
  return `missions.${session.user?.username ?? "anonimo"}`;
}

export function missionFlags() {
  return prefs.get(storageKey(), {});
}

/** Marca una misión «de acción» como hecha y avisa a la interfaz. */
export function markDone(flag) {
  const flags = missionFlags();
  if (flags[flag]) return;
  prefs.set(storageKey(), { ...flags, [flag]: true });
  window.dispatchEvent(new CustomEvent("mission", { detail: flag }));
}

/**
 * Avisa con `notify(texto)` cada vez que se cumple una misión, sin tener que volver al
 * inicio. La primera evaluación solo memoriza lo que ya estaba hecho.
 */
export function watchMissions(onCollectionChange, notify) {
  let known = null;
  let ready = false; // hasta tener la colección no se sabe qué estaba hecho
  let collection = [];
  const evaluate = () => {
    if (!ready) return;
    const done = new Set(evaluateMissions({ collection, flags: missionFlags() }).filter((m) => m.done).map((m) => m.id));
    if (known) {
      for (const mission of MISSIONS) {
        if (done.has(mission.id) && !known.has(mission.id)) notify(`Misión cumplida: ${mission.title}`);
      }
    }
    known = done;
  };
  onCollectionChange((entries) => {
    if (!entries) {
      known = null; // sesión cerrada: el próximo usuario empieza de cero
      ready = false;
      return;
    }
    collection = entries;
    ready = true;
    evaluate();
  });
  window.addEventListener("mission", evaluate);
}
