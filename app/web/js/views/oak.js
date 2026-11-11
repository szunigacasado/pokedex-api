// Profesor Oak.
//
// - La página #/oak: la nota de cada equipo (calculada por la API) y el chat con Oak a
//   pantalla completa. El chat es el mismo que el del panel lateral (oak-chat.js).
// - Piezas que usa la página del equipo: el consejo redactado por la IA y los Pokémon
//   concretos que arreglan sus puntos débiles.

import { request } from "../api.js";
import { askOak, createChat, reviewCard } from "../oak-chat.js";
import { el } from "../dom.js";
import { artworkUrl, displayName } from "../format.js";
import { addToCollection, entryFor, loadCollection } from "../data.js";
import { icon } from "../icons.js";
import { markDone } from "../missions.js";
import { sfx } from "../sound.js";
import { busy, empty, loader, showError, toast, typeBadge, typeBadges } from "../ui.js";

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

// ═════════════════════════════════════════════════════════════
//  Página
// ═════════════════════════════════════════════════════════════
function teamGrades(teams) {
  const detail = el("div", { "aria-live": "polite" });
  const buttons = teams.map((team) => {
    const score = el("span", { class: "team-grade__score" }, "…");
    const button = el(
      "button",
      { type: "button", class: "team-grade", "aria-pressed": "false" },
      el("span", { class: "team-grade__name" }, team.name),
      el("span", { class: "team-pick__members", "aria-hidden": "true" }, team.members.map((m) => el("img", { src: artworkUrl(m.pokemon_id), alt: "", width: 28, height: 28 }))),
      score,
    );
    const review = request(`/api/v1/teams/${team.id}/review`);
    review.then((r) => (score.textContent = String(r.score).replace(".", ","))).catch(() => (score.textContent = "–"));
    button.addEventListener("click", async () => {
      for (const b of buttons) b.setAttribute("aria-pressed", String(b === button));
      try {
        detail.replaceChildren(
          reviewCard(await review, team.name),
          el(
            "div",
            { class: "row" },
            el("button", { type: "button", class: "btn btn--primary btn--small", onClick: () => askOak(`¿Qué Pokémon le falta a mi equipo «${team.name}»?`, { team_id: team.id, pokemon_id: null }) }, icon("bulb"), "¿Qué le falta?"),
            el("a", { class: "btn btn--secondary btn--small", href: `#/equipos/${team.id}` }, icon("swords"), "Abrir"),
          ),
        );
      } catch (error) {
        showError(error);
      }
    });
    return button;
  });
  if (buttons.length) queueMicrotask(() => buttons[0].click());
  return el("div", { class: "stack" }, el("div", { class: "team-grades", role: "group", "aria-label": "Tus equipos" }, buttons), detail);
}

export async function renderOak(view) {
  view.replaceChildren(loader("Abriendo el laboratorio…"));
  const teams = (await request("/api/v1/teams")).filter((t) => t.members.length);
  view.replaceChildren(
    el("div", { class: "page-head" }, el("div", {}, el("h1", {}, "Profesor Oak"), el("p", {}, "Pídele que revise tu equipo, que te recomiende Pokémon o que busque por ti. Consulta la API con tu sesión y tú decides si haces lo que te propone."))),
    el(
      "div",
      { class: "oak-page" },
      el("section", { class: "panel stack oak-page__chat", "aria-labelledby": "oak-chat-title" }, el("h2", { id: "oak-chat-title", class: "visually-hidden" }, "Chat con el Profesor Oak"), createChat()),
      el(
        "aside",
        { class: "panel stack oak-page__side", "aria-labelledby": "oak-grades-title" },
        el("h2", { id: "oak-grades-title" }, icon("medal"), "Nota de tus equipos"),
        teams.length
          ? teamGrades(teams)
          : empty("swords", "Aún no tienes equipos", "Crea uno y Oak le pondrá nota.", el("a", { class: "btn btn--primary", href: "#/equipos/nuevo" }, icon("plus"), "Crear un equipo")),
      ),
    ),
  );
}
