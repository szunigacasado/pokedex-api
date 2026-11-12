// «Ruta del entrenador»: los cinco pasos que guían a quien empieza. Función pura (se
// prueba con node --test): recibe lo que ya ha hecho y devuelve cada paso con su estado.

export const STARTERS = [1, 4, 7]; // Bulbasaur, Charmander y Squirtle
export const CAPTURE_GOAL = 3;

export const ROUTE = [
  { id: "starter", icon: "pokeball", title: "Elige tu primer Pokémon", text: "Tu compañero de aventura. Se añade a tu Pokédex ya capturado." },
  { id: "capture", icon: "search", title: `Captura ${CAPTURE_GOAL - 1} Pokémon más`, text: "Para formar un equipo necesitas compañeros. Elige entre estos o busca el que quieras." },
  { id: "team", icon: "swords", title: "Arma tu equipo", text: "Junta a tus Pokémon en un equipo: es con lo que combatirás." },
  { id: "review", icon: "flask", title: "Pide su nota al Profesor Oak", text: "Te dice de 0 a 10 cómo de equilibrado está tu equipo y qué le falta." },
  { id: "battle", icon: "bolt", title: "Gana tu primer combate", text: "Tu equipo contra un rival de la máquina. Ganando, tus Pokémon suben de nivel." },
];

/**
 * Estado de la ruta.
 * @param {object} progress  {collection, teams, flags, wins}
 * @returns {{steps: object[], current: object|null, done: number, finished: boolean}}
 */
export function trainerRoute({ collection = [], teams = 0, flags = {}, wins = 0 } = {}) {
  const captured = collection.filter((e) => e.is_captured).length;
  const checks = {
    starter: collection.length > 0,
    capture: captured >= CAPTURE_GOAL,
    team: teams > 0,
    review: Boolean(flags.advice),
    battle: wins > 0 || Boolean(flags.battle),
  };
  // Los pasos se hacen en orden: uno no cuenta como hecho si falta alguno anterior
  let blocked = false;
  const steps = ROUTE.map((step) => {
    const done = !blocked && checks[step.id];
    const state = done ? "done" : blocked ? "locked" : "current";
    if (!done) blocked = true;
    return { ...step, state };
  });
  const done = steps.filter((s) => s.state === "done").length;
  return { steps, current: steps.find((s) => s.state === "current") ?? null, done, finished: done === steps.length, captured };
}
