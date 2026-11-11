// Mi Pokédex: tu colección con filtros, orden, edición de cada entrada y exportación.

import { download } from "../api.js";
import { el } from "../dom.js";
import { dexNumber, displayName, formatDate, plural } from "../format.js";
import { loadCollection, onCollectionChange, releaseEntry, updateEntry } from "../data.js";
import { sfx } from "../sound.js";
import { busy, empty, loader, openModal, pokemonCard, showError, toast, typeBadges } from "../ui.js";
import { badgeLegend, collectionBadge } from "./search.js";
import { icon } from "../icons.js";

const FILTERS = [
  ["all", "Todos"],
  ["captured", "Capturados"],
  ["pending", "Por capturar"],
  ["favorite", "Favoritos"],
];

const SORTS = {
  number: ["Número", (a, b) => a.pokemon_id - b.pokemon_id],
  name: ["Nombre", (a, b) => displayName(a.pokemon_name).localeCompare(displayName(b.pokemon_name), "es")],
  captured: ["Fecha de captura", (a, b) => (b.capture_date ?? "").localeCompare(a.capture_date ?? "")],
  recent: ["Añadidos recientemente", (a, b) => b.created_at.localeCompare(a.created_at)],
};

export function kantoProgress(entries) {
  const captured = entries.filter((e) => e.is_captured && e.pokemon_id <= 151).length;
  const bar = el("progress", { class: "progress", max: 151, value: captured, "aria-label": "Pokémon de Kanto capturados" });
  return el(
    "div",
    { class: "progress-box" },
    el("div", { class: "row row--between" }, el("strong", {}, "Kanto"), el("span", {}, `${captured} de 151 capturados`)),
    bar,
  );
}

function editor(entry, close) {
  const problem = el("div", { class: "alert", role: "alert", hidden: true });
  const nickname = el("input", { class: "input", id: "edit-nickname", maxlength: 50, value: entry.nickname ?? "", placeholder: "Sin apodo" });
  const notes = el("textarea", { class: "textarea", id: "edit-notes", maxlength: 500, placeholder: "Dónde lo encontraste, qué te gusta de él…" }, entry.notes ?? "");
  const captured = el("input", { type: "checkbox", id: "edit-captured", checked: entry.is_captured });
  const favorite = el("input", { type: "checkbox", id: "edit-favorite", checked: entry.favorite });
  const save = el("button", { type: "submit", class: "btn btn--primary" }, "Guardar");
  const release = el("button", { type: "button", class: "btn btn--ghost danger" }, "Liberar");
  let confirming = false;

  release.addEventListener("click", () => {
    if (!confirming) {
      confirming = true;
      release.textContent = "¿Seguro? Pulsa otra vez para liberarlo";
      return;
    }
    busy(release, async () => {
      try {
        await releaseEntry(entry.id);
        toast(`${displayName(entry.pokemon_name)} ha vuelto a la naturaleza.`, { type: "ok" });
        close();
      } catch (error) {
        showError(error);
      }
    });
  });

  const form = el(
    "form",
    { class: "stack", "aria-label": "Editar" },
    el(
      "div",
      { class: "row" },
      entry.pokemon_sprite ? el("img", { src: entry.pokemon_sprite, alt: "", width: 96, height: 96 }) : null,
      el("div", {}, el("p", { class: "muted" }, dexNumber(entry.pokemon_id)), el("h2", {}, displayName(entry.pokemon_name)), typeBadges(entry.pokemon_types)),
    ),
    problem,
    el("div", { class: "row" }, el("label", { class: "switch", for: "edit-captured" }, captured, "Capturado"), el("label", { class: "switch", for: "edit-favorite" }, favorite, "Favorito")),
    entry.capture_date ? el("p", { class: "muted" }, `Capturado el ${formatDate(entry.capture_date)}.`) : null,
    el("div", { class: "field" }, el("label", { for: "edit-nickname" }, "Apodo"), nickname),
    el("div", { class: "field" }, el("label", { for: "edit-notes" }, "Notas"), notes),
    el("div", { class: "row row--between" }, el("a", { class: "btn btn--secondary", href: `#/pokemon/${entry.pokemon_id}` }, "Ver su ficha"), el("div", { class: "row" }, release, save)),
  );

  form.addEventListener("submit", (event) => {
    event.preventDefault();
    busy(save, async () => {
      try {
        await updateEntry(entry.id, {
          nickname: nickname.value.trim() || null,
          notes: notes.value.trim() || null,
          is_captured: captured.checked,
          favorite: favorite.checked,
        });
        sfx("success");
        toast("Cambios guardados.", { type: "ok" });
        close();
      } catch (error) {
        problem.textContent = error.message;
        problem.hidden = false;
      }
    }, "Guardando…");
  });
  return form;
}

export async function renderCollection(view) {
  view.replaceChildren(loader("Abriendo tu Pokédex…"));
  let latest = await loadCollection();
  const state = { filter: "all", sort: "number" };
  const draw = () => drawFrom(latest);

  const body = el("div", { class: "stack" });
  const filterButtons = FILTERS.map(([value, label]) => {
    const button = el("button", { type: "button", "aria-pressed": String(value === state.filter) }, label);
    button.addEventListener("click", () => {
      state.filter = value;
      for (const b of filterButtons) b.setAttribute("aria-pressed", String(b === button));
      draw();
    });
    return button;
  });
  const sort = el("select", { class: "select", id: "collection-sort" }, Object.entries(SORTS).map(([value, [label]]) => el("option", { value }, label)));
  sort.addEventListener("change", () => {
    state.sort = sort.value;
    draw();
  });

  const exportButton = (format, label) => {
    const button = el("button", { type: "button", class: "btn btn--secondary" }, icon("download"), label);
    button.addEventListener("click", () =>
      busy(button, async () => {
        try {
          const query = { format };
          if (state.filter === "captured") query.captured = true;
          if (state.filter === "pending") query.captured = false;
          if (state.filter === "favorite") query.favorite = true;
          const name = await download("/api/v1/pokedex/export", query);
          toast(`Descargado: ${name}`, { type: "ok" });
        } catch (error) {
          showError(error);
        }
      }, "Preparando…"),
    );
    return button;
  };

  // Se vuelve a pintar cada vez que cambia la colección (también desde otra pantalla).
  const drawFrom = (all) => {
    if (!all) return;
    const filtered = all
      .filter((e) => {
        if (state.filter === "captured") return e.is_captured;
        if (state.filter === "pending") return !e.is_captured;
        if (state.filter === "favorite") return e.favorite;
        return true;
      })
      .sort(SORTS[state.sort][1]);

    if (all.length === 0) {
      body.replaceChildren(empty("book", "Tu Pokédex está vacía", "Busca un Pokémon y pulsa «Añadir a mi Pokédex».", el("a", { class: "btn btn--primary", href: "#/buscar" }, icon("search"), "Ir al buscador")));
      return;
    }
    body.replaceChildren(
      kantoProgress(all),
      el("div", { class: "row row--between" }, el("p", { class: "muted", role: "status" }, `${plural(filtered.length, "Pokémon", "Pokémon")} en esta vista.`), badgeLegend()),
      filtered.length
        ? el(
            "div",
            { class: "grid" },
            filtered.map((entry) =>
              pokemonCard({
                id: entry.pokemon_id,
                name: entry.nickname ? `${entry.nickname}` : entry.pokemon_name,
                sprite: entry.pokemon_sprite,
                types: entry.pokemon_types,
                badge: collectionBadge(entry.pokemon_id),
                label: `${entry.nickname ? `${entry.nickname}, ` : ""}${displayName(entry.pokemon_name)}: editar`,
                onClick: () => {
                  const dialog = openModal(editor(entry, () => dialog.close()), { label: `Editar ${displayName(entry.pokemon_name)}` });
                },
              }),
            ),
          )
        : el("p", { class: "empty" }, "No hay Pokémon con este filtro."),
    );
  };

  const stop = onCollectionChange((all) => {
    latest = all;
    drawFrom(all);
  });
  window.addEventListener("hashchange", stop, { once: true });

  view.replaceChildren(
    el("div", { class: "page-head" }, el("div", {}, el("h1", {}, "Mi Pokédex"), el("p", {}, "Toca un Pokémon para ponerle apodo, notas o marcarlo como capturado.")), el("div", { class: "row" }, exportButton("csv", "CSV"), exportButton("pdf", "PDF"))),
    el(
      "div",
      { class: "panel row row--between" },
      el("div", { class: "segmented segmented--4", role: "group", "aria-label": "Filtrar" }, filterButtons),
      el("div", { class: "field field--inline" }, el("label", { for: "collection-sort" }, "Ordenar por"), sort),
    ),
    body,
  );
  drawFrom(latest);
}
