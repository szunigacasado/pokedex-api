// Profesor Oak: consejo sobre un equipo y búsqueda en lenguaje natural.
// La IA solo redacta o interpreta; los datos los calcula siempre la API.

import { request } from "../api.js";
import { el } from "../dom.js";
import { markDone } from "../missions.js";
import { busy, loader, pokemonCard, showError } from "../ui.js";
import { collectionBadge } from "./search.js";
import { icon } from "../icons.js";

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

/** Pide consejo sobre un equipo y lo pinta en `slot`. */
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

export async function renderOak(view) {
  const input = el("input", { class: "input input--search", id: "oak-question", maxlength: 200, minlength: 3, autocomplete: "off", placeholder: "Escribe lo que buscas…" });
  const ask = el("button", { type: "submit", class: "btn btn--primary" }, "Preguntar");
  const answer = el("div", { class: "stack", "aria-live": "polite" });

  async function run(question) {
    input.value = question;
    await busy(ask, async () => {
      answer.replaceChildren(loader("Consultando mis notas…"));
      try {
        const result = await request("/api/v1/pokemon/ask", { query: { q: question } });
        markDone("ask");
        answer.replaceChildren(
          oakBubble(
            result.count
              ? `He encontrado ${result.count === 1 ? "un Pokémon" : `${result.count} Pokémon`} que encajan. ${result.explanation}`
              : `No conozco ningún Pokémon de Kanto así. ${result.explanation}`,
            sourceNote(result.source, result.model),
          ),
          result.results.length
            ? el("div", { class: "grid" }, result.results.map((p) => pokemonCard({ ...p, href: `#/pokemon/${p.id}`, badge: collectionBadge(p.id) })))
            : null,
        );
      } catch (error) {
        answer.replaceChildren();
        showError(error);
      }
    }, "Pensando…");
  }

  const form = el("form", { class: "panel stack", role: "search", "aria-label": "Pregúntale al Profesor Oak" },
    el("label", { for: "oak-question" }, el("strong", {}, "¿Qué Pokémon buscas? Escríbelo como se lo dirías a una persona.")),
    el("div", { class: "input-row" }, input, ask),
    el("div", { class: "row" }, el("span", { class: "muted" }, "Prueba con:"), EXAMPLES.map((example) =>
      el("button", { type: "button", class: "btn btn--secondary btn--small", onClick: () => run(example) }, example),
    )),
  );
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    if (input.value.trim().length < 3) {
      input.focus();
      return;
    }
    run(input.value.trim());
  });

  view.replaceChildren(
    el("div", { class: "page-head" }, el("div", {}, el("h1", {}, "Pregúntale al Profesor Oak"), el("p", {}, "Busca entre los 151 de Kanto con tus propias palabras."))),
    oakBubble("¡Hola! Dime qué tipo de Pokémon buscas y lo encontraré en mis notas. La IA solo entiende tu pregunta: los resultados salen siempre de los datos reales."),
    form,
    answer,
  );
  input.focus();
}
