// Buscador: los 151 de Kanto al instante, con filtro por tipo y por colección, y búsqueda
// en todas las generaciones cuando el Pokémon no es de Kanto.

import { request } from "../api.js";
import { debounce, el } from "../dom.js";
import { TYPES, matchesQuery, normalize, plural, typeClass } from "../format.js";
import { entryFor, loadCollection, loadKanto } from "../data.js";
import { busy, loader, pokemonCard, showError } from "../ui.js";
import { icon } from "../icons.js";

const OWNERSHIP = [
  ["all", "Todos"],
  ["mine", "En mi Pokédex"],
  ["missing", "Me faltan"],
];

/** Icono de la esquina de la tarjeta según su estado en la colección. */
export function collectionBadge(pokemonId) {
  const entry = entryFor(pokemonId);
  if (!entry) return null;
  if (entry.favorite) return icon("star", { fill: true });
  return icon("pokeball", { fill: entry.is_captured });
}

export function badgeLegend() {
  return el(
    "p",
    { class: "legend muted" },
    icon("pokeball", { fill: true }),
    "capturado · ",
    icon("pokeball"),
    "en tu Pokédex sin capturar · ",
    icon("star", { fill: true }),
    "favorito",
  );
}

export async function renderSearch(view) {
  view.replaceChildren(loader("Encendiendo el buscador…"));
  const [catalog] = await Promise.all([loadKanto(), loadCollection()]);

  const state = { query: "", types: new Set(), ownership: "all" };
  const input = el("input", {
    class: "input input--search",
    id: "search-input",
    type: "search",
    placeholder: "Pikachu, 25, char…",
    autocomplete: "off",
    "aria-describedby": "search-hint",
  });
  const counter = el("p", { class: "muted", role: "status" });
  const grid = el("div", { class: "grid", id: "search-results" });
  const beyond = el("section", { class: "stack", "aria-live": "polite" });

  const typeButtons = Object.entries(TYPES).map(([type, label]) => {
    const button = el("button", { type: "button", class: `type-chip ${typeClass(type)}`, "aria-pressed": "false" }, label);
    button.addEventListener("click", () => {
      if (state.types.has(type)) state.types.delete(type);
      else state.types.add(type);
      button.setAttribute("aria-pressed", String(state.types.has(type)));
      update();
    });
    return button;
  });

  const ownershipButtons = OWNERSHIP.map(([value, label]) => {
    const button = el("button", { type: "button", "aria-pressed": String(value === state.ownership) }, label);
    button.addEventListener("click", () => {
      state.ownership = value;
      for (const b of ownershipButtons) b.setAttribute("aria-pressed", String(b === button));
      update();
    });
    return button;
  });

  function visible() {
    return catalog.filter((p) => {
      if (!matchesQuery(p, state.query)) return false;
      if (state.types.size && !p.types.some((t) => state.types.has(t))) return false;
      const entry = entryFor(p.id);
      if (state.ownership === "mine" && !entry) return false;
      if (state.ownership === "missing" && entry?.is_captured) return false;
      return true;
    });
  }

  function update() {
    const list = visible();
    counter.textContent = `Mostrando ${plural(list.length, "Pokémon", "Pokémon")} de los 151 de Kanto.`;
    grid.replaceChildren(
      ...list.map((p) => pokemonCard({ ...p, href: `#/pokemon/${p.id}`, badge: collectionBadge(p.id) })),
    );
    renderBeyond();
  }

  // Fuera de Kanto: consulta PokeAPI, así que solo se hace si el usuario lo pide.
  function renderBeyond() {
    const q = normalize(state.query);
    if (q.length < 2 || /^#?\d+$/.test(q)) {
      beyond.replaceChildren();
      return;
    }
    const button = el("button", { type: "button", class: "btn btn--secondary" }, `Buscar «${state.query}» en todas las generaciones`);
    button.addEventListener("click", () =>
      busy(button, async () => {
        try {
          const result = await request("/api/v1/pokemon/search", { query: { name: q.replace(/\s+/g, "-"), limit: 24 } });
          const others = result.results.filter((p) => p.id > 151);
          beyond.replaceChildren(
            el("h2", {}, "De otras generaciones"),
            others.length
              ? el("div", { class: "grid" }, others.map((p) => pokemonCard({ ...p, href: `#/pokemon/${p.id}`, badge: collectionBadge(p.id) })))
              : el("p", { class: "muted" }, "No hay más Pokémon con ese nombre."),
          );
        } catch (error) {
          showError(error);
        }
      }, "Buscando…"),
    );
    beyond.replaceChildren(button);
  }

  input.addEventListener("input", debounce(() => {
    state.query = input.value;
    update();
  }, 150));

  view.replaceChildren(
    el(
      "div",
      { class: "page-head" },
      el("div", {}, el("h1", {}, "Buscar Pokémon"), el("p", {}, "Toca una tarjeta para ver su ficha y añadirlo a tu Pokédex.")),
    ),
    el(
      "div",
      { class: "panel stack search-controls" },
      el(
        "div",
        { class: "field" },
        el("label", { for: "search-input" }, "Nombre o número"),
        input,
        el("span", { class: "field__hint", id: "search-hint" }, "La lista se filtra mientras escribes."),
      ),
      el("div", { class: "segmented segmented--3", role: "group", "aria-label": "Qué Pokémon mostrar" }, ownershipButtons),
      el("div", { role: "group", "aria-label": "Filtrar por tipo" }, el("p", { class: "field__hint" }, "Filtra por tipo (puedes elegir varios):"), el("div", { class: "type-chips" }, typeButtons)),
    ),
    el("div", { class: "row row--between" }, counter, badgeLegend()),
    grid,
    beyond,
  );
  update();
  input.focus();
}
