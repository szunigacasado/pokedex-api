// Ficha de un Pokémon: ilustración (y variocolor), descripción, estadísticas, cadena
// evolutiva y los controles de tu colección.

import { download } from "../api.js";
import { el } from "../dom.js";
import {
  STAT_LABELS,
  artworkUrl,
  dexNumber,
  displayName,
  heightLabel,
  spriteUrl,
  typeClass,
  weightLabel,
} from "../format.js";
import { addToCollection, entryFor, loadCollection, loadDetail, loadEvolution, onCollectionChange, updateEntry } from "../data.js";
import { markDone } from "../missions.js";
import { sfx } from "../sound.js";
import { busy, loader, showError, toast, typeBadges } from "../ui.js";
import { icon } from "../icons.js";

/** Piezas extra de la ficha (voz y grito, en otra rama). Reciben (detail, slot). */
export const pokemonExtras = [];

const STAT_MAX = 255;

export function statBars(stats) {
  const total = Object.values(stats).reduce((a, b) => a + b, 0);
  return el(
    "dl",
    { class: "stats" },
    Object.entries(STAT_LABELS).map(([key, label]) => {
      const value = Math.round(stats[key] ?? 0);
      return el(
        "div",
        { class: "stat" },
        el("dt", {}, label),
        el("dd", {}, el("span", { class: "stat__value" }, String(value)), el("span", { class: `stat__bar stat__bar--${value >= 100 ? "high" : value >= 60 ? "mid" : "low"}`, style: { "--w": `${Math.min(100, (value / STAT_MAX) * 100)}%` }, "aria-hidden": "true" })),
      );
    }),
    el("div", { class: "stat stat--total" }, el("dt", {}, "Total"), el("dd", {}, el("span", { class: "stat__value" }, String(Math.round(total))))),
  );
}

function artPanel(detail) {
  const types = detail.types;
  let shiny = false;
  const img = el("img", { class: "art__img", src: detail.sprites.official_artwork ?? artworkUrl(detail.id), alt: `Ilustración de ${displayName(detail.name)}`, width: 320, height: 320 });
  const sparkle = el("div", { class: "art__sparkle", "aria-hidden": "true" });
  const button = el("button", { type: "button", class: "btn btn--secondary btn--small art__shiny", "aria-pressed": "false" }, icon("sparkles"), "Variocolor");
  button.addEventListener("click", () => {
    shiny = !shiny;
    img.src = artworkUrl(detail.id, { shiny });
    img.alt = `Ilustración ${shiny ? "variocolor " : ""}de ${displayName(detail.name)}`;
    button.setAttribute("aria-pressed", String(shiny));
    sparkle.classList.remove("is-active");
    void sparkle.offsetWidth; // reinicia la animación
    if (shiny) {
      sparkle.classList.add("is-active");
      markDone("shiny");
    }
  });
  return el("div", { class: `art ${typeClass(types[0])}` }, el("span", { class: "art__number" }, dexNumber(detail.id)), img, sparkle, button);
}

function evolutionChain(chain, currentId) {
  if (!chain || chain.steps.length < 2) {
    return el("p", { class: "muted" }, "No evoluciona.");
  }
  const byStage = new Map();
  for (const step of chain.steps) byStage.set(step.stage, [...(byStage.get(step.stage) ?? []), step]);
  const stages = [...byStage.keys()].sort((a, b) => a - b);
  return el(
    "ol",
    { class: "evolution" },
    stages.map((stage, index) =>
      el(
        "li",
        { class: "evolution__stage" },
        index > 0 ? el("span", { class: "evolution__arrow" }, icon("arrow-right")) : null,
        el(
          "div",
          { class: "evolution__options" },
          byStage.get(stage).map((step) =>
            el(
              "a",
              { class: "evo", href: `#/pokemon/${step.id}`, "aria-current": step.id === currentId ? "true" : null },
              el("img", { src: spriteUrl(step.id), alt: "", width: 72, height: 72, loading: "lazy" }),
              el("span", { class: "evo__name" }, displayName(step.name)),
              step.condition ? el("span", { class: "evo__condition" }, step.condition) : null,
            ),
          ),
        ),
      ),
    ),
  );
}

function collectionControls(detail) {
  const box = el("section", { class: "panel stack", "aria-labelledby": "mine-title" });

  const render = () => {
    const entry = entryFor(detail.id);
    if (!entry) {
      const add = el("button", { type: "button", class: "btn btn--primary btn--block" }, icon("plus"), "Añadir a mi Pokédex");
      const caught = el("button", { type: "button", class: "btn btn--secondary btn--block" }, icon("pokeball"), "Lo he capturado");
      const action = (extra, message) => async (event) =>
        busy(event.currentTarget, async () => {
          try {
            await addToCollection(detail.id, extra);
            sfx("capture");
            toast(message, { type: "ok" });
          } catch (error) {
            showError(error);
          }
        });
      add.addEventListener("click", action({}, `${displayName(detail.name)} está en tu Pokédex.`));
      caught.addEventListener("click", action({ is_captured: true }, `¡${displayName(detail.name)} capturado!`));
      box.replaceChildren(el("h2", { id: "mine-title" }, "Tu Pokédex"), el("p", { class: "muted" }, "Aún no lo tienes."), add, caught);
      return;
    }
    const toggle = (field, iconName, on, off) => {
      const button = el(
        "button",
        { type: "button", class: "btn btn--secondary btn--block", "aria-pressed": String(entry[field]) },
        icon(iconName, { fill: entry[field] }),
        entry[field] ? on : off,
      );
      button.addEventListener("click", () =>
        busy(button, async () => {
          try {
            await updateEntry(entry.id, { [field]: !entry[field] });
            if (!entry[field]) sfx(field === "favorite" ? "success" : "capture");
          } catch (error) {
            showError(error);
          }
        }),
      );
      return button;
    };
    box.replaceChildren(
      el("h2", { id: "mine-title" }, "Tu Pokédex"),
      el("p", {}, entry.nickname ? `Lo llamas «${entry.nickname}».` : "Está en tu colección."),
      toggle("is_captured", "pokeball", "Capturado", "Marcar como capturado"),
      toggle("favorite", "star", "Favorito", "Marcar como favorito"),
      el("a", { class: "btn btn--ghost btn--block", href: "#/pokedex" }, "Editar apodo y notas en Mi Pokédex"),
    );
  };

  const stop = onCollectionChange(render);
  window.addEventListener("hashchange", stop, { once: true });
  render();
  return box;
}

export async function renderPokemon(view, { id }) {
  view.replaceChildren(loader("Consultando la Pokédex…"));
  const [detail] = await Promise.all([loadDetail(id), loadCollection()]);
  const chain = await loadEvolution(detail.id);
  const species = detail.species;
  const name = species?.display_name ?? displayName(detail.name);

  const download_ = el("button", { type: "button", class: "btn btn--secondary btn--block" }, icon("download"), "Descargar su ficha (PNG)");
  download_.addEventListener("click", () =>
    busy(download_, async () => {
      try {
        await download(`/api/v1/pokemon/${detail.id}/card`, { format: "png" });
      } catch (error) {
        showError(error);
      }
    }, "Preparando la ficha…"),
  );

  const extrasSlot = el("div", { class: "row" });
  for (const extra of pokemonExtras) extra(detail, extrasSlot);

  const info = [
    ["Altura", heightLabel(detail.height)],
    ["Peso", weightLabel(detail.weight)],
    ["Habilidades", detail.abilities.map((a) => `${displayName(a.name)}${a.is_hidden ? " (oculta)" : ""}`).join(", ")],
  ];

  view.replaceChildren(
    el("a", { class: "back-link", href: "#/buscar" }, icon("arrow-left"), "Volver al buscador"),
    el(
      "div",
      { class: "detail" },
      el("div", { class: "detail__side stack" }, artPanel(detail), collectionControls(detail), download_),
      el(
        "div",
        { class: "detail__main stack" },
        el(
          "header",
          {},
          el("p", { class: "muted detail__number" }, dexNumber(detail.id)),
          el("h1", {}, name),
          species?.genus ? el("p", { class: "detail__genus" }, species.genus) : null,
          typeBadges(detail.types),
        ),
        species?.description ? el("blockquote", { class: "pokedex-entry" }, species.description) : null,
        extrasSlot,
        el("dl", { class: "facts" }, info.map(([k, v]) => el("div", {}, el("dt", {}, k), el("dd", {}, v)))),
        el("section", { class: "stack" }, el("h2", {}, "Estadísticas base"), statBars(detail.stats)),
        el("section", { class: "stack" }, el("h2", {}, "Evolución"), evolutionChain(chain, detail.id)),
      ),
    ),
  );
}
