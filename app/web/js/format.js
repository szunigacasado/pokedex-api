// Textos y formatos de la interfaz. Funciones puras: no tocan el DOM ni la red, así que
// se prueban con `node --test` (tests/web).

export const TYPES = Object.freeze({
  normal: "Normal",
  fire: "Fuego",
  water: "Agua",
  electric: "Eléctrico",
  grass: "Planta",
  ice: "Hielo",
  fighting: "Lucha",
  poison: "Veneno",
  ground: "Tierra",
  flying: "Volador",
  psychic: "Psíquico",
  bug: "Bicho",
  rock: "Roca",
  ghost: "Fantasma",
  dragon: "Dragón",
  dark: "Siniestro",
  steel: "Acero",
  fairy: "Hada",
});

export const STAT_LABELS = Object.freeze({
  hp: "PS",
  attack: "Ataque",
  defense: "Defensa",
  special_attack: "At. Esp.",
  special_defense: "Def. Esp.",
  speed: "Velocidad",
});

export function typeLabel(type) {
  return Object.hasOwn(TYPES, type ?? "") ? TYPES[type] : capitalize(type ?? "");
}

/** Clase CSS del color del tipo; «normal» si el tipo no se conoce. */
export function typeClass(type) {
  return `t-${Object.hasOwn(TYPES, type ?? "") ? type : "normal"}`;
}

export function capitalize(text) {
  return text ? text.charAt(0).toUpperCase() + text.slice(1) : "";
}

/** 25 → «#025» */
export function dexNumber(id) {
  return `#${String(id).padStart(3, "0")}`;
}

const SPECIAL_NAMES = Object.freeze({
  "nidoran-f": "Nidoran♀",
  "nidoran-m": "Nidoran♂",
  "mr-mime": "Mr. Mime",
  "mr-rime": "Mr. Rime",
  "mime-jr": "Mime Jr.",
  farfetchd: "Farfetch’d",
  sirfetchd: "Sirfetch’d",
  "ho-oh": "Ho-Oh",
  "porygon-z": "Porygon-Z",
  "type-null": "Código Cero",
  "jangmo-o": "Jangmo-o",
  "hakamo-o": "Hakamo-o",
  "kommo-o": "Kommo-o",
});

/** Nombre de PokeAPI («mr-mime», «charizard») a nombre para mostrar. */
export function displayName(name) {
  if (!name) return "";
  if (Object.hasOwn(SPECIAL_NAMES, name)) return SPECIAL_NAMES[name];
  return name.split("-").map(capitalize).join(" ");
}

export function plural(count, singular, pluralForm = `${singular}s`) {
  return `${count} ${count === 1 ? singular : pluralForm}`;
}

const DATE_FORMAT = new Intl.DateTimeFormat("es-ES", { day: "numeric", month: "long", year: "numeric" });

export function formatDate(value) {
  if (!value) return "";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "" : DATE_FORMAT.format(date);
}

// ── Contraseñas: la misma política que aplica la API (app/models.py) ──────────
const encoder = new TextEncoder();

export function passwordChecks(password, username = "") {
  const value = password ?? "";
  const user = (username ?? "").trim().toLowerCase();
  return [
    { id: "length", label: "Al menos 8 caracteres", ok: value.length >= 8 },
    { id: "upper", label: "Una letra mayúscula", ok: /\p{Lu}/u.test(value) },
    { id: "digit", label: "Un número", ok: /\d/.test(value) },
    {
      id: "username",
      label: "Que no contenga tu nombre de usuario",
      ok: value.length > 0 && !(user.length > 0 && value.toLowerCase().includes(user)),
    },
    { id: "bytes", label: "Como mucho 72 bytes", ok: encoder.encode(value).length <= 72 },
  ];
}

export function passwordIsValid(password, username) {
  return passwordChecks(password, username).every((check) => check.ok);
}

/** Usuarios: 3-50 caracteres, minúsculas, números, punto, guion o guion bajo. */
export function usernameProblem(username) {
  const value = (username ?? "").trim();
  if (value.length < 3) return "Usa al menos 3 caracteres.";
  if (value.length > 50) return "Usa como mucho 50 caracteres.";
  if (!/^[a-zA-Z0-9_.-]+$/.test(value)) return "Solo letras sin acentos, números, punto, guion y guion bajo.";
  return null;
}

// ── Errores de la API en lenguaje normal ─────────────────────────────────────
const FIELD_LABELS = Object.freeze({
  username: "El nombre de usuario",
  email: "El email",
  password: "La contraseña",
  nickname: "El apodo",
  notes: "Las notas",
  name: "El nombre",
  description: "La descripción",
  pokemon_ids: "La lista de Pokémon",
  q: "La pregunta",
});

const TYPE_MESSAGES = Object.freeze({
  missing: "es obligatorio",
  string_too_short: "es demasiado corto",
  string_too_long: "es demasiado largo",
  too_short: "tiene muy pocos elementos",
  too_long: "tiene demasiados elementos",
  value_error: "no es válido",
  string_pattern_mismatch: "tiene caracteres no permitidos",
  int_parsing: "debe ser un número",
  greater_than_equal: "es demasiado pequeño",
  less_than_equal: "es demasiado grande",
});

/** Un error de validación de la API ({loc, type, msg}) a una frase en español. */
export function validationMessage(error) {
  const field = [...(error?.loc ?? [])].reverse().find((part) => typeof part === "string" && part !== "body");
  const label = Object.hasOwn(FIELD_LABELS, field ?? "") ? FIELD_LABELS[field] : "Un dato";
  const msg = String(error?.msg ?? "");
  // Las reglas propias de la API ya vienen en español: «Value error, La contraseña…»
  if (msg.startsWith("Value error, ")) return msg.slice("Value error, ".length);
  const problem = Object.hasOwn(TYPE_MESSAGES, error?.type ?? "") ? TYPE_MESSAGES[error.type] : "no es válido";
  return `${label} ${problem}.`;
}

/** Mensaje para el usuario a partir del código HTTP y el cuerpo de la respuesta. */
export function friendlyError(status, body, retryAfter = null) {
  const detail = body?.detail;
  if (status === 0) return "No hay conexión con la Pokédex. ¿Está el servidor en marcha?";
  if (status === 429) {
    const seconds = Number(retryAfter);
    return Number.isFinite(seconds) && seconds > 0
      ? `Vas muy rápido. Espera ${plural(Math.ceil(seconds), "segundo")} y vuelve a intentarlo.`
      : "Vas muy rápido. Espera un momento y vuelve a intentarlo.";
  }
  if (status === 422 && Array.isArray(detail) && detail.length > 0) {
    return [...new Set(detail.map(validationMessage))].join(" ");
  }
  if (typeof detail === "string" && detail) return detail;
  if (status === 401) return "Tu sesión ha terminado. Vuelve a entrar.";
  if (status === 403) return "No tienes permiso para hacer esto.";
  if (status === 404) return "No se ha encontrado.";
  if (status === 413) return "Lo que intentas enviar es demasiado grande.";
  if (status >= 500) return "La Pokédex ha tenido un problema. Inténtalo de nuevo en un momento.";
  return "No se ha podido completar la acción.";
}

/** Nombre del fichero en una cabecera Content-Disposition. */
export function filenameFromDisposition(header, fallback = "descarga") {
  const match = /filename\*?=(?:UTF-8'')?"?([^";]+)"?/i.exec(header ?? "");
  return match ? decodeURIComponent(match[1]) : fallback;
}
