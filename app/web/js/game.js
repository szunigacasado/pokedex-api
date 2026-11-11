// Lógica de «Adivina la silueta». Funciones puras con un generador de números
// aleatorios inyectable, así que se prueban con node --test y una semilla fija.

export const ROUNDS = 10;
export const OPTIONS = 4;

/** Generador pseudoaleatorio con semilla (mulberry32), para tests reproducibles. */
export function seededRandom(seed) {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

export function shuffle(items, random = Math.random) {
  const copy = [...items];
  for (let i = copy.length - 1; i > 0; i -= 1) {
    const j = Math.floor(random() * (i + 1));
    [copy[i], copy[j]] = [copy[j], copy[i]];
  }
  return copy;
}

/**
 * Una ronda: el Pokémon a adivinar y cuatro opciones barajadas. Para que no sea
 * demasiado fácil, se prefieren señuelos que compartan algún tipo con la respuesta.
 * `used` evita repetir respuesta dentro de la misma partida.
 */
export function newRound(catalog, { random = Math.random, used = new Set() } = {}) {
  const candidates = catalog.filter((p) => !used.has(p.id));
  const answer = candidates[Math.floor(random() * candidates.length)];
  const others = catalog.filter((p) => p.id !== answer.id);
  const similar = shuffle(others.filter((p) => p.types.some((t) => answer.types.includes(t))), random);
  const rest = shuffle(others.filter((p) => !similar.includes(p)), random);
  const decoys = [...similar.slice(0, 2), ...rest].slice(0, OPTIONS - 1);
  return { answer, options: shuffle([answer, ...decoys], random) };
}

/** Frase final según la puntuación. */
export function verdict(score, total = ROUNDS) {
  const ratio = score / total;
  if (ratio === 1) return "¡Perfecto! Eres un auténtico Maestro Pokédex.";
  if (ratio >= 0.7) return "¡Muy bien! Conoces a casi todos.";
  if (ratio >= 0.4) return "¡No está mal! Repasa la Pokédex y vuelve a intentarlo.";
  return "¡Ánimo! Cada partida aprenderás a reconocer más siluetas.";
}
