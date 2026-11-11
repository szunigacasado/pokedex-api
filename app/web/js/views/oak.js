// Laboratorio del Profesor Oak.
//
// - «Mejora tu equipo»: Pokémon concretos que arreglan sus puntos débiles (calculados con
//   la tabla de tipos, sin IA) con botón para meterlos en el equipo, y el consejo que
//   redacta la IA.
// - «Pregúntale»: búsqueda en lenguaje natural que enseña cómo ha entendido la pregunta
//   y deja añadir cada resultado a la Pokédex.

import { request } from "../api.js";
import { el } from "../dom.js";
import { STAT_LABELS, artworkUrl, displayName, typeLabel } from "../format.js";
import { addToCollection, entryFor, loadCollection } from "../data.js";
import { icon } from "../icons.js";
import { markDone } from "../missions.js";
import { sfx } from "../sound.js";
import { busy, empty, loader, showError, toast, typeBadge, typeBadges } from "../ui.js";

const EXAMPLES = [
  "los 3 Pokémon de fuego más rápidos",
  "dragones que no sean legendarios",
  "un tanque de agua que aguante golpes especiales",
  "tipo planta sin veneno con más de 80 de defensa",
  "los 5 legendarios más fuertes",
];

const reducedMotion = () => window.matchMedia("(prefers-reduced-motion: reduce)").matches;

/** Escribe el texto letra a letra, como en los juegos (salvo con «reducir movimiento»). */
function typewriter(node, text) {
  if (reducedMotion()) {
    node.textContent = text;
    return;
  }
  node.textContent = "";
  node.setAttribute("aria-label", text); // el lector de pantalla lee la frase entera
  let i = 0;
  const timer = setInterval(() => {
    i += 2;
    node.textContent = text.slice(0, i);
    if (i >= text.length || !node.isConnected) clearInterval(timer);
  }, 18);
}

function sourceNote(source, model) {
  return el(
    "p",
    { class: "oak__source" },
    icon(source === "ai" ? "sparkles" : "rules"),
    source === "ai"
      ? `Redactado por IA (${model}) a partir de datos calculados por la API.`
      : "Generado con reglas a partir de datos calculados por la API (IA no disponible).",
  );
}

/** Bocadillo del Profesor Oak. `body` se añade debajo del texto principal. */
export function oakBubble(text, ...body) {
  const speech = el("p", { class: "oak__text" });
  const node = el(
    "div",
    { class: "oak" },
    el("div", { class: "oak__avatar" }, icon("flask")),
    el("div", { class: "oak__bubble" }, el("p", { class: "oak__name" }, "Profesor Oak"), speech, ...body),
  );
  typewriter(speech, text);
  return node;
}

function adviceList(title, iconName, items) {
  if (!items?.length) return null;
  return el("section", { class: "oak__list" }, el("h3", {}, icon(iconName), title), el("ul", {}, items.map((item) => el("li", {}, item))));
}

/** Pide consejo sobre un equipo (IA o reglas) y lo pinta en `slot`. */
export async function showTeamAdvice(teamId, slot) {
  slot.replaceChildren(loader("El Profesor Oak está estudiando tu equipo…"));
  try {
    const advice = await request(`/api/v1/teams/${teamId}/advice`);
    markDone("advice");
    slot.replaceChildren(
      oakBubble(
        advice.summary,
        el(
          "div",
          { class: "oak__lists" },
          adviceList("Puntos fuertes", "shield", advice.strengths),
          adviceList("Puntos débiles", "warning", advice.weaknesses),
          adviceList("Mis consejos", "bulb", advice.suggestions),
        ),
        sourceNote(advice.source, advice.model),
      ),
    );
  } catch (error) {
    slot.replaceChildren();
    showError(error);
  }
}

// ═════════════════════════════════════════════════════════════
//  Tarjeta de un Pokémon con acciones
// ═════════════════════════════════════════════════════════════
function ownership(pokemonId) {
  const entry = entryFor(pokemonId);
  if (!entry) return el("span", { class: "chip chip--muted" }, "No lo tienes");
  return entry.is_captured
    ? el("span", { class: "chip chip--ok" }, icon("pokeball", { fill: true }), "Capturado")
    : el("span", { class: "chip" }, icon("pokeball"), "En tu Pokédex");
}

/** Tarjeta compacta: imagen, nombre, tipos, motivo y botones. */
function pokemonTile(pokemon, { reason = null, extra = null, actions = [] } = {}) {
  return el(
    "article",
    { class: "tile-pokemon" },
    el(
      "a",
      { class: "tile-pokemon__link", href: `#/pokemon/${pokemon.id}` },
      el("img", { src: artworkUrl(pokemon.id), alt: "", width: 88, height: 88, loading: "lazy" }),
      el("span", { class: "tile-pokemon__name" }, displayName(pokemon.name)),
    ),
    typeBadges(pokemon.types),
    reason ? el("p", { class: "tile-pokemon__reason" }, icon("check"), reason) : null,
    extra,
    ownership(pokemon.id),
    actions.length ? el("div", { class: "tile-pokemon__actions" }, actions) : null,
  );
}

function addToPokedexButton(pokemon, onDone) {
  if (entryFor(pokemon.id)) return null;
  const button = el("button", { type: "button", class: "btn btn--secondary btn--small" }, icon("plus"), "A mi Pokédex");
  button.addEventListener("click", () =>
    busy(button, async () => {
      try {
        await addToCollection(pokemon.id);
        sfx("capture");
        toast(`${displayName(pokemon.name)} está en tu Pokédex.`, { type: "ok" });
        onDone?.();
      } catch (error) {
        showError(error);
      }
    }),
  );
  return button;
}

// ═════════════════════════════════════════════════════════════
//  Mejora tu equipo
// ═════════════════════════════════════════════════════════════
/**
 * Recomendaciones concretas para un equipo. `onTeamChange` se llama tras meter a un
 * Pokémon en el equipo, para volver a calcular todo.
 */
export async function recommendationsSection(team, { onTeamChange } = {}) {
  const [data] = await Promise.all([request(`/api/v1/teams/${team.id}/recommendations`), loadCollection()]);
  if (!data.recommendations.length) {
    return el("p", { class: "alert alert--ok" }, "¡Tu equipo no tiene puntos débiles graves ni huecos en su cobertura!");
  }

  const addToTeam = (pokemon) => {
    const button = el("button", { type: "button", class: "btn btn--primary btn--small" }, icon("plus"), "Al equipo");
    button.addEventListener("click", () =>
      busy(button, async () => {
        try {
          if (!entryFor(pokemon.id)) await addToCollection(pokemon.id);
          const ids = [...team.members.map((m) => m.pokemon_id), pokemon.id];
          await request(`/api/v1/teams/${team.id}`, { method: "PUT", json: { pokemon_ids: ids } });
          sfx("capture");
          toast(`${displayName(pokemon.name)} se une a «${team.name}».`, { type: "ok" });
          onTeamChange?.();
        } catch (error) {
          showError(error);
        }
      }, "Añadiendo…"),
    );
    return button;
  };

  const full = data.free_slots === 0;
  const refresh = () => onTeamChange?.();
  return el(
    "div",
    { class: "stack" },
    full
      ? el("p", { class: "alert alert--info" }, "Tu equipo está completo: para meter a uno de estos, quita antes a alguien en el constructor.")
      : el("p", { class: "muted" }, `Te quedan ${data.free_slots} ${data.free_slots === 1 ? "hueco libre" : "huecos libres"}. Primero salen los que ya tienes.`),
    data.recommendations.map((rec) =>
      el(
        "section",
        { class: `rec rec--${rec.kind}` },
        el(
          "header",
          { class: "rec__head" },
          el("span", { class: "rec__icon" }, icon(rec.kind === "defense" ? "shield" : "target")),
          el("div", {}, el("h3", {}, rec.title), el("p", { class: "muted" }, rec.detail)),
        ),
        el("div", { class: "row rec__types" }, el("span", { class: "muted" }, "Tipos que sirven:"), rec.useful_types.map(typeBadge)),
        el(
          "div",
          { class: "tiles-pokemon" },
          rec.pokemon.map((p) =>
            pokemonTile(p, { reason: p.reason, actions: [full ? addToPokedexButton(p, refresh) : addToTeam(p)].filter(Boolean) }),
          ),
        ),
      ),
    ),
  );
}

async function teamHelp(team, area, rerender) {
  const recs = el("div", {}, loader("Buscando Pokémon que encajen…"));
  const advice = el("div", { "aria-live": "polite" });
  area.replaceChildren(
    el(
      "div",
      { class: "row row--between team-strip" },
      el("div", { class: "row" }, team.members.map((m) => el("img", { src: artworkUrl(m.pokemon_id), alt: displayName(m.pokemon_name), width: 56, height: 56 }))),
      el("a", { class: "btn btn--secondary btn--small", href: `#/equipos/${team.id}` }, icon("swords"), "Abrir en el constructor"),
    ),
    el("h2", {}, icon("bulb"), "Pokémon que te recomiendo"),
    recs,
    el("h2", {}, icon("message"), "Mi opinión"),
    advice,
  );
  recommendationsSection(team, { onTeamChange: rerender })
    .then((node) => recs.replaceChildren(node))
    .catch((error) => {
      recs.replaceChildren();
      showError(error);
    });
  showTeamAdvice(team.id, advice);
}

async function teamTool(panel, initial = null) {
  panel.replaceChildren(loader("Buscando tus equipos…"));
  const teams = await request("/api/v1/teams");
  if (!teams.length) {
    panel.replaceChildren(
      empty("swords", "Primero crea un equipo", "Cuando tengas uno, te diré qué Pokémon le faltan.", el("a", { class: "btn btn--primary", href: "#/equipos/nuevo" }, icon("plus"), "Crear un equipo")),
    );
    return;
  }
  const area = el("div", { class: "stack" });
  let selected = teams.some((t) => t.id === initial) ? initial : teams[0].id;

  async function show() {
    try {
      const team = await request(`/api/v1/teams/${selected}`);
      // Al cambiar el equipo se vuelve a pintar todo, también las miniaturas de arriba.
      await teamHelp(team, area, () => teamTool(panel, selected));
    } catch (error) {
      showError(error);
    }
  }

  const buttons = teams.map((team) => {
    const button = el(
      "button",
      { type: "button", class: "team-pick", "aria-pressed": String(team.id === selected) },
      el("span", { class: "team-pick__name" }, team.name),
      el("span", { class: "team-pick__members", "aria-hidden": "true" }, team.members.map((m) => el("img", { src: artworkUrl(m.pokemon_id), alt: "", width: 32, height: 32 }))),
    );
    button.addEventListener("click", () => {
      selected = team.id;
      for (const b of buttons) b.setAttribute("aria-pressed", String(b === button));
      show();
    });
    return button;
  });

  panel.replaceChildren(el("div", { class: "team-picks", role: "group", "aria-label": "Elige un equipo" }, buttons), area);
  show();
}

// ═════════════════════════════════════════════════════════════
//  Pregúntale
// ═════════════════════════════════════════════════════════════
/** «Así lo he entendido»: los filtros de la pregunta como etiquetas legibles. */
export function understoodChips(query) {
  const chips = [];
  for (const t of query.types ?? []) chips.push(typeBadge(t));
  for (const t of query.exclude_types ?? []) chips.push(el("span", { class: "chip" }, icon("ban"), `sin ${typeLabel(t)}`));
  for (const [stat, value] of Object.entries(query.min_stats ?? {})) chips.push(el("span", { class: "chip" }, `${STAT_LABELS[stat] ?? stat} ≥ ${value}`));
  for (const [stat, value] of Object.entries(query.max_stats ?? {})) chips.push(el("span", { class: "chip" }, `${STAT_LABELS[stat] ?? stat} ≤ ${value}`));
  if (query.legendary === true) chips.push(el("span", { class: "chip" }, "solo legendarios"));
  if (query.legendary === false) chips.push(el("span", { class: "chip" }, "sin legendarios"));
  if (query.mythical === true) chips.push(el("span", { class: "chip" }, "solo míticos"));
  if (query.mythical === false) chips.push(el("span", { class: "chip" }, "sin míticos"));
  if (query.sort_by) {
    const label = query.sort_by === "total" ? "estadística total" : (STAT_LABELS[query.sort_by] ?? query.sort_by).toLowerCase();
    chips.push(el("span", { class: "chip" }, `por ${label}, ${query.order === "asc" ? "de menor a mayor" : "de mayor a menor"}`));
  }
  chips.push(el("span", { class: "chip chip--muted" }, `hasta ${query.limit}`));
  return chips;
}

function askTool(panel) {
  const input = el("input", { class: "input input--search", id: "oak-question", maxlength: 200, minlength: 3, autocomplete: "off", placeholder: "Escribe lo que buscas…" });
  const ask = el("button", { type: "submit", class: "btn btn--primary" }, icon("message"), "Preguntar");
  const answer = el("div", { class: "stack", "aria-live": "polite" });

  async function run(question) {
    input.value = question;
    await busy(ask, async () => {
      answer.replaceChildren(loader("Consultando mis notas…"));
      try {
        const [result] = await Promise.all([request("/api/v1/pokemon/ask", { query: { q: question } }), loadCollection()]);
        markDone("ask");
        const grid = el("div", {});
        const draw = () =>
          grid.replaceChildren(
            el(
              "div",
              { class: "tiles-pokemon" },
              result.results.map((p) => pokemonTile(p, { extra: el("p", { class: "muted" }, `Total ${p.total}`), actions: [addToPokedexButton(p, draw)].filter(Boolean) })),
            ),
          );
        draw();
        answer.replaceChildren(
          oakBubble(
            result.count
              ? `He encontrado ${result.count === 1 ? "un Pokémon" : `${result.count} Pokémon`} que encajan.`
              : "No conozco ningún Pokémon de Kanto así. Prueba a pedir algo menos concreto.",
            el("div", { class: "understood" }, el("span", { class: "muted" }, "Así lo he entendido:"), understoodChips(result.interpreted)),
            sourceNote(result.source, result.model),
          ),
          result.results.length ? grid : null,
        );
      } catch (error) {
        answer.replaceChildren();
        showError(error);
      }
    }, "Pensando…");
  }

  const form = el(
    "form",
    { class: "panel stack", role: "search", "aria-label": "Pregúntale al Profesor Oak" },
    el("label", { for: "oak-question" }, el("strong", {}, "¿Qué Pokémon buscas? Escríbelo como se lo dirías a una persona.")),
    el("div", { class: "input-row" }, input, ask),
    el("div", { class: "row" }, el("span", { class: "muted" }, "Prueba con:"), EXAMPLES.map((example) => el("button", { type: "button", class: "btn btn--secondary btn--small", onClick: () => run(example) }, example))),
  );
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    if (input.value.trim().length < 3) {
      input.focus();
      return;
    }
    run(input.value.trim());
  });
  panel.replaceChildren(form, answer);
  input.focus();
}

// ═════════════════════════════════════════════════════════════
//  Página
// ═════════════════════════════════════════════════════════════
export async function renderOak(view) {
  const panel = el("div", { class: "stack" });
  const tools = [
    ["Mejora tu equipo", "swords", () => teamTool(panel)],
    ["Pregúntale", "message", () => askTool(panel)],
  ];
  const tabs = tools.map(([label, iconName, open]) => {
    const button = el("button", { type: "button", "aria-pressed": "false" }, icon(iconName), label);
    button.addEventListener("click", () => {
      for (const b of tabs) b.setAttribute("aria-pressed", String(b === button));
      open();
    });
    return button;
  });

  view.replaceChildren(
    el("div", { class: "page-head" }, el("div", {}, el("h1", {}, "Laboratorio del Profesor Oak"), el("p", {}, "Te digo qué Pokémon le faltan a tu equipo y busco por ti con tus palabras."))),
    oakBubble("¡Bienvenido a mi laboratorio! Elige un equipo y te diré qué Pokémon concretos lo harían mejor, o pregúntame lo que quieras sobre los 151 de Kanto. Los datos los calcula siempre la API: yo solo te los explico."),
    el("div", { class: "segmented", role: "group", "aria-label": "Herramientas del laboratorio" }, tabs),
    panel,
  );
  tabs[0].click();
}
