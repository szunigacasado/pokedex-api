// Equipos: lista, constructor con seis huecos (arrastrar y soltar o botones), análisis
// visual y consejo del Profesor Oak.

import { download, request } from "../api.js";
import { el } from "../dom.js";
import { artworkUrl, displayName, matchesQuery, plural, typeLabel } from "../format.js";
import { radar, weaknessHeatmap } from "../charts.js";
import { loadCollection } from "../data.js";
import { markDone } from "../missions.js";
import { sfx } from "../sound.js";
import { navigate } from "../router.js";
import { busy, empty, loader, showError, toast, typeBadge, typeBadges } from "../ui.js";
import { recommendationsSection, showTeamAdvice } from "./oak.js";
import { icon } from "../icons.js";

const TEAM_SIZE = 6;

// ═════════════════════════════════════════════════════════════
//  Lista de equipos
// ═════════════════════════════════════════════════════════════
function teamCard(team) {
  return el(
    "a",
    { class: "team-card", href: `#/equipos/${team.id}` },
    el("h2", {}, team.name),
    team.description ? el("p", { class: "muted" }, team.description) : null,
    el(
      "div",
      { class: "team-card__members" },
      Array.from({ length: TEAM_SIZE }, (_, i) => {
        const m = team.members[i];
        return m
          ? el("img", { src: artworkUrl(m.pokemon_id), alt: displayName(m.pokemon_name), width: 56, height: 56, loading: "lazy" })
          : el("span", { class: "team-card__empty", "aria-hidden": "true" });
      }),
    ),
    el("p", { class: "muted" }, `${team.members.length} de ${TEAM_SIZE}`),
  );
}

export async function renderTeams(view) {
  view.replaceChildren(loader("Buscando tus equipos…"));
  const teams = await request("/api/v1/teams");
  const create = el("a", { class: "btn btn--primary", href: "#/equipos/nuevo" }, icon("plus"), "Nuevo equipo");
  view.replaceChildren(
    el("div", { class: "page-head" }, el("div", {}, el("h1", {}, "Mis equipos"), el("p", {}, "Junta hasta seis Pokémon de tu colección y descubre sus puntos fuertes y débiles.")), create),
    teams.length
      ? el("div", { class: "grid grid--wide" }, teams.map(teamCard))
      : empty("swords", "Aún no tienes equipos", "Crea el primero: elige hasta seis Pokémon de tu Pokédex.", el("a", { class: "btn btn--primary", href: "#/equipos/nuevo" }, icon("plus"), "Crear mi primer equipo")),
  );
}

// ═════════════════════════════════════════════════════════════
//  Constructor
// ═════════════════════════════════════════════════════════════
/** Pokémon concretos que recomienda Oak; al meter uno, se recalcula toda la página. */
function recommendationsBlock(team) {
  const slot = el("div", {}, loader("Buscando Pokémon que encajen…"));
  recommendationsSection(team, { onTeamChange: () => navigate(`/equipos/${team.id}`) })
    .then((node) => slot.replaceChildren(node))
    .catch((error) => {
      slot.replaceChildren();
      showError(error);
    });
  return el("section", { class: "panel stack" }, el("h3", {}, icon("bulb"), "Pokémon que te recomienda el Profesor Oak"), slot);
}

function analysisSection(analysis, team) {
  const pdf = el("button", { type: "button", class: "btn btn--secondary" }, icon("download"), "Descargar informe PDF");
  pdf.addEventListener("click", () =>
    busy(pdf, async () => {
      try {
        await download(`/api/v1/teams/${team.id}/export`);
      } catch (error) {
        showError(error);
      }
    }, "Preparando el PDF…"),
  );
  const adviceSlot = el("div", { "aria-live": "polite" });
  const adviceButton = el("button", { type: "button", class: "btn btn--primary" }, icon("flask"), "Pedir consejo al Profesor Oak");
  adviceButton.addEventListener("click", () => busy(adviceButton, () => showTeamAdvice(team.id, adviceSlot), "Consultando…"));

  const chips = (iconName, title, types, kind) =>
    el("div", { class: "stack stack--tight" }, el("h3", {}, icon(iconName), title), types.length ? el("div", { class: "types" }, types.map(typeBadge)) : el("p", { class: "muted" }, kind));

  return el(
    "section",
    { class: "stack analysis", "aria-labelledby": "analysis-title" },
    el("div", { class: "row row--between" }, el("h2", { id: "analysis-title" }, "Análisis del equipo"), el("div", { class: "row" }, adviceButton, pdf)),
    adviceSlot,
    el(
      "div",
      { class: "analysis__top" },
      el("figure", { class: "panel radar-box" }, radar(analysis.combined.average), el("figcaption", { class: "muted" }, `Estadísticas medias · total base medio ${Math.round(analysis.combined.average_base_stat_total)}`)),
      el(
        "div",
        { class: "panel stack" },
        el("h3", {}, "Conclusiones"),
        el("ul", { class: "highlights" }, analysis.highlights.map((h) => el("li", {}, h))),
        chips("warning", "Debilidades compartidas", analysis.shared_weaknesses, "Ninguna: las debilidades están repartidas."),
        chips("target", "Golpea con daño doble a", analysis.super_effective_against, "Ningún tipo."),
        chips("ban", "Sin cobertura ofensiva contra", analysis.not_covered, "¡Cubre todos los tipos!"),
      ),
    ),
    recommendationsBlock(team),
    el("section", { class: "panel stack" }, el("h3", {}, "Mapa de debilidades"), el("p", { class: "muted" }, "Cuánto daño recibe cada miembro de cada tipo de ataque. Arriba, los ataques más peligrosos para tu equipo."), weaknessHeatmap(analysis)),
  );
}

export async function renderTeamEditor(view, { id }) {
  view.replaceChildren(loader("Preparando el constructor…"));
  const isNew = id === undefined;
  const [collection, team] = await Promise.all([loadCollection(), isNew ? null : request(`/api/v1/teams/${id}`)]);
  const byPokemon = new Map(collection.map((e) => [e.pokemon_id, e]));

  const state = {
    members: team ? team.members.map((m) => m.pokemon_id) : [],
    dirty: false,
  };

  const name = el("input", { class: "input", id: "team-name", maxlength: 100, required: true, value: team?.name ?? "", placeholder: "Por ejemplo: Equipo Élite" });
  const description = el("textarea", { class: "textarea", id: "team-description", maxlength: 500, placeholder: "¿Para qué es este equipo? (opcional)" }, team?.description ?? "");
  const save = el("button", { type: "submit", class: "btn btn--primary" }, isNew ? "Crear equipo" : "Guardar cambios");
  const status = el("p", { class: "muted", role: "status" });
  const slots = el("ol", { class: "slots", "aria-label": "Huecos del equipo" });
  const slotsTitle = el("h2", { id: "slots-title" });
  const palette = el("div", { class: "palette" });
  const filter = el("input", { class: "input", id: "palette-filter", type: "search", placeholder: "Filtrar tu colección…", autocomplete: "off" });
  const analysisSlot = el("div", {});

  const markDirty = () => {
    state.dirty = true;
    status.textContent = "Tienes cambios sin guardar.";
  };

  function add(pokemonId) {
    if (state.members.includes(pokemonId)) return toast("Ese Pokémon ya está en el equipo.", { type: "warn" });
    if (state.members.length >= TEAM_SIZE) return toast("El equipo ya tiene seis Pokémon. Quita uno antes.", { type: "warn" });
    state.members.push(pokemonId);
    markDirty();
    drawSlots();
    drawPalette();
  }

  function move(from, to) {
    if (to < 0 || to >= state.members.length) return;
    const [item] = state.members.splice(from, 1);
    state.members.splice(to, 0, item);
    markDirty();
    drawSlots();
  }

  function remove(index) {
    state.members.splice(index, 1);
    markDirty();
    drawSlots();
    drawPalette();
  }

  function drawSlots() {
    slotsTitle.textContent = `Tu equipo · ${plural(state.members.length, "Pokémon", "Pokémon")} de ${TEAM_SIZE}`;
    slots.replaceChildren(
      ...Array.from({ length: TEAM_SIZE }, (_, i) => {
        const pokemonId = state.members[i];
        const entry = byPokemon.get(pokemonId);
        const slot = el("li", { class: `slot ${pokemonId ? "slot--filled" : ""}`, dataset: { index: String(i) } });
        slot.addEventListener("dragover", (event) => {
          event.preventDefault();
          slot.classList.add("slot--over");
        });
        slot.addEventListener("dragleave", () => slot.classList.remove("slot--over"));
        slot.addEventListener("drop", (event) => {
          event.preventDefault();
          slot.classList.remove("slot--over");
          const data = event.dataTransfer.getData("text/plain");
          if (data.startsWith("slot:")) move(Number(data.slice(5)), Math.min(i, state.members.length - 1));
          else add(Number(data));
        });
        if (!pokemonId) {
          slot.append(el("span", { class: "slot__empty" }, icon("plus"), el("span", {}, `Hueco ${i + 1} libre`)));
          return slot;
        }
        slot.draggable = true;
        slot.addEventListener("dragstart", (event) => event.dataTransfer.setData("text/plain", `slot:${i}`));
        const label = entry?.nickname ?? displayName(entry?.pokemon_name ?? String(pokemonId));
        slot.append(
          el("img", { src: artworkUrl(pokemonId), alt: "", width: 72, height: 72 }),
          el("span", { class: "slot__name" }, label),
          typeBadges(entry?.pokemon_types ?? []),
          el(
            "div",
            { class: "slot__actions" },
            el("button", { type: "button", class: "btn btn--secondary btn--small", "aria-label": `Mover a ${label} a la izquierda`, disabled: i === 0, onClick: () => move(i, i - 1) }, icon("chevron-left")),
            el("button", { type: "button", class: "btn btn--secondary btn--small", "aria-label": `Quitar a ${label} del equipo`, onClick: () => remove(i) }, icon("x")),
            el("button", { type: "button", class: "btn btn--secondary btn--small", "aria-label": `Mover a ${label} a la derecha`, disabled: i === state.members.length - 1, onClick: () => move(i, i + 1) }, icon("chevron-right")),
          ),
        );
        return slot;
      }),
    );
  }

  function drawPalette() {
    const available = collection.filter((e) => matchesQuery({ id: e.pokemon_id, name: e.nickname ?? e.pokemon_name }, filter.value) || matchesQuery({ id: e.pokemon_id, name: e.pokemon_name }, filter.value));
    if (!collection.length) {
      palette.replaceChildren(empty("book", "Tu Pokédex está vacía", "Los equipos se forman con Pokémon de tu colección.", el("a", { class: "btn btn--primary", href: "#/buscar" }, icon("search"), "Buscar Pokémon")));
      return;
    }
    palette.replaceChildren(
      ...available.map((entry) => {
        const inTeam = state.members.includes(entry.pokemon_id);
        const label = entry.nickname ?? displayName(entry.pokemon_name);
        const button = el(
          "button",
          { type: "button", class: "chip-pokemon", draggable: "true", disabled: inTeam, "aria-label": inTeam ? `${label} ya está en el equipo` : `Añadir a ${label} al equipo` },
          el("img", { src: artworkUrl(entry.pokemon_id), alt: "", width: 44, height: 44, loading: "lazy" }),
          el("span", {}, label),
          el("span", { class: "visually-hidden" }, entry.pokemon_types.map(typeLabel).join(", ")),
        );
        button.addEventListener("dragstart", (event) => event.dataTransfer.setData("text/plain", String(entry.pokemon_id)));
        button.addEventListener("click", () => add(entry.pokemon_id));
        return button;
      }),
    );
  }

  filter.addEventListener("input", drawPalette);

  async function loadAnalysis(teamId) {
    analysisSlot.replaceChildren(loader("Analizando el equipo…"));
    try {
      const [analysis, saved] = await Promise.all([request(`/api/v1/teams/${teamId}/analysis`), request(`/api/v1/teams/${teamId}`)]);
      analysisSlot.replaceChildren(analysisSection(analysis, saved));
    } catch (error) {
      analysisSlot.replaceChildren();
      showError(error);
    }
  }

  const form = el(
    "form",
    { class: "panel stack", "aria-label": "Datos del equipo" },
    el("div", { class: "field" }, el("label", { for: "team-name" }, "Nombre del equipo"), name),
    el("div", { class: "field" }, el("label", { for: "team-description" }, "Descripción"), description),
    el("div", { class: "row row--between" }, status, el("div", { class: "row" }, isNew ? null : el("button", { type: "button", class: "btn btn--ghost danger", id: "delete-team" }, "Borrar equipo"), save)),
  );
  name.addEventListener("input", markDirty);
  description.addEventListener("input", markDirty);

  form.addEventListener("submit", (event) => {
    event.preventDefault();
    if (!name.value.trim()) {
      toast("Ponle un nombre al equipo.", { type: "warn" });
      name.focus();
      return;
    }
    if (!state.members.length) {
      toast("Añade al menos un Pokémon al equipo.", { type: "warn" });
      return;
    }
    const payload = { name: name.value.trim(), description: description.value.trim() || null, pokemon_ids: state.members };
    busy(save, async () => {
      try {
        if (isNew) {
          const created = await request("/api/v1/teams", { method: "POST", json: payload });
          markDone("team");
          sfx("success");
          toast(`¡Equipo «${created.name}» creado!`, { type: "ok" });
          navigate(`/equipos/${created.id}`);
          return;
        }
        await request(`/api/v1/teams/${id}`, { method: "PUT", json: payload });
        state.dirty = false;
        status.textContent = "Guardado.";
        sfx("success");
        toast("Equipo guardado.", { type: "ok" });
        loadAnalysis(id);
      } catch (error) {
        showError(error);
      }
    }, "Guardando…");
  });

  let confirming = false;
  form.querySelector("#delete-team")?.addEventListener("click", (event) => {
    const button = event.currentTarget;
    if (!confirming) {
      confirming = true;
      button.textContent = "¿Seguro? Pulsa otra vez para borrarlo";
      return;
    }
    busy(button, async () => {
      try {
        await request(`/api/v1/teams/${id}`, { method: "DELETE" });
        toast("Equipo borrado.", { type: "ok" });
        navigate("/equipos");
      } catch (error) {
        showError(error);
      }
    });
  });

  view.replaceChildren(
    el("a", { class: "back-link", href: "#/equipos" }, icon("arrow-left"), "Volver a mis equipos"),
    el("h1", {}, isNew ? "Nuevo equipo" : team.name),
    el(
      "div",
      { class: "builder" },
      el(
        "div",
        { class: "stack" },
        el("section", { class: "stack", "aria-labelledby": "slots-title" }, slotsTitle, slots),
        form,
      ),
      el(
        "section",
        { class: "panel stack", "aria-labelledby": "palette-title" },
        el("h2", { id: "palette-title" }, "Tu colección"),
        el("p", { class: "muted" }, "Arrastra un Pokémon a un hueco, o tócalo para añadirlo."),
        el("label", { class: "visually-hidden", for: "palette-filter" }, "Filtrar tu colección"),
        filter,
        palette,
      ),
    ),
    analysisSlot,
  );

  drawSlots();
  drawPalette();
  if (!isNew) loadAnalysis(id);
}
