// Inicio guiado: la «Ruta del entrenador».
//
// Quien empieza ve un solo paso cada vez, con una tarjeta grande y la acción hecha ahí
// mismo (sin ir a otra pantalla). Los pasos hechos quedan como una línea de marcas y los
// siguientes, bloqueados. Al terminar, el inicio pasa a ser un panel con tres accesos.
// Las misiones de siempre siguen, plegadas en «Logros».

import { request } from "../api.js";
import { el } from "../dom.js";
import { artworkUrl, displayName } from "../format.js";
import { addToCollection, entryFor, kantoById, loadCollection, loadKanto, updateEntry } from "../data.js";
import { icon } from "../icons.js";
import { evaluateMissions, markDone, missionFlags } from "../missions.js";
import { openOak, reviewCard } from "../oak-chat.js";
import { navigate } from "../router.js";
import { session } from "../session.js";
import { playCry, sfx } from "../sound.js";
import { CAPTURE_GOAL, ROUTE, STARTERS, trainerRoute } from "../trainer-route.js";
import { busy, loader, showError, toast, typeBadges } from "../ui.js";
import { kantoProgress } from "./collection.js";

// Sugerencias para el paso de capturar: conocidos y de tipos variados
const SUGGESTED = [25, 133, 39, 52, 54, 63, 66, 74, 92, 129, 143, 147];

const again = () => navigate("/"); // vuelve a pintar el inicio con el progreso nuevo

// ═════════════════════════════════════════════════════════════
//  Pasos
// ═════════════════════════════════════════════════════════════
function pokemonChoice(pokemon, label, onPick) {
  const button = el(
    "button",
    { type: "button", class: "choice" },
    el("img", { src: artworkUrl(pokemon.id), alt: "", width: 96, height: 96, loading: "lazy" }),
    el("strong", {}, displayName(pokemon.name)),
    typeBadges(pokemon.types),
    el("span", { class: "choice__action" }, icon("pokeball"), label),
  );
  button.addEventListener("click", () => busy(button, () => onPick(pokemon), "Capturando…"));
  return button;
}

async function capture(pokemon) {
  try {
    const existing = entryFor(pokemon.id);
    if (existing) await updateEntry(existing.id, { is_captured: true });
    else await addToCollection(pokemon.id, { is_captured: true });
    sfx("capture");
    playCry(pokemon.id, { volume: 0.5 });
    toast(`¡${displayName(pokemon.name)} capturado!`, { type: "ok" });
    again();
  } catch (error) {
    showError(error);
  }
}

async function starterStep() {
  await loadKanto();
  return el("div", { class: "choices choices--3" }, STARTERS.map((id) => pokemonChoice(kantoById(id), "Elegir", capture)));
}

async function captureStep(progress) {
  await loadKanto();
  const pending = progress.collection.filter((e) => !e.is_captured).map((e) => kantoById(e.pokemon_id)).filter(Boolean);
  const owned = new Set(progress.collection.map((e) => e.pokemon_id));
  const fresh = SUGGESTED.filter((id) => !owned.has(id)).map(kantoById).filter(Boolean).slice(0, 6 - Math.min(pending.length, 3));
  return el(
    "div",
    { class: "stack" },
    el("p", { class: "route-card__count" }, `${progress.captured} de ${CAPTURE_GOAL} capturados`),
    el("div", { class: "choices" }, [...pending.slice(0, 3), ...fresh].map((p) => pokemonChoice(p, "Capturar", capture))),
    el("a", { class: "btn btn--ghost btn--small", href: "#/buscar" }, icon("search"), "Prefiero buscar otro"),
  );
}

async function teamStep(progress) {
  await loadKanto();
  const members = progress.collection.filter((e) => e.is_captured).slice(0, 6);
  const create = el("button", { type: "button", class: "btn btn--primary btn--big" }, icon("swords"), "Crear mi equipo");
  create.addEventListener("click", () =>
    busy(create, async () => {
      try {
        await request("/api/v1/teams", { method: "POST", json: { name: "Mi primer equipo", pokemon_ids: members.map((e) => e.pokemon_id) } });
        sfx("success");
        toast("¡Equipo creado!", { type: "ok" });
        again();
      } catch (error) {
        showError(error);
      }
    }, "Creando…"),
  );
  return el(
    "div",
    { class: "stack" },
    el("div", { class: "team-preview", "aria-label": "Tu equipo" }, members.map((e) => el("figure", {}, el("img", { src: artworkUrl(e.pokemon_id), alt: "", width: 72, height: 72 }), el("figcaption", {}, e.nickname ?? displayName(e.pokemon_name))))),
    el("div", { class: "row" }, create, el("a", { class: "btn btn--ghost btn--small", href: "#/equipos/nuevo" }, "Prefiero elegirlos yo")),
  );
}

async function reviewStep(progress) {
  const team = progress.teams[0];
  const review = await request(`/api/v1/teams/${team.id}/review`);
  const next = el("button", { type: "button", class: "btn btn--primary" }, icon("check"), "¡Entendido! Siguiente paso");
  next.addEventListener("click", () => {
    markDone("advice");
    again();
  });
  const ask = el("button", { type: "button", class: "btn btn--secondary" }, icon("message"), "¿Qué le falta?");
  ask.addEventListener("click", () => {
    markDone("advice");
    openOak(`¿Qué Pokémon le falta a mi equipo «${team.name}»?`);
  });
  return el("div", { class: "stack" }, reviewCard(review, team.name), el("div", { class: "row" }, next, ask));
}

async function battleStep(progress) {
  const team = progress.teams[0];
  const go = el("button", { type: "button", class: "btn btn--primary btn--big" }, icon("bolt"), "¡A combatir!");
  go.addEventListener("click", () =>
    busy(go, async () => {
      try {
        const battle = await request("/api/v1/battles", { method: "POST", json: { team_id: team.id, difficulty: "easy" } });
        navigate(`/combate/${battle.id}`);
      } catch (error) {
        showError(error);
      }
    }, "Buscando rival…"),
  );
  return el(
    "div",
    { class: "stack" },
    el("p", {}, progress.losses ? "Esta vez no ha salido bien. ¡Vuelve a intentarlo!" : `Empiezas con «${team.name}» contra un rival fácil.`),
    el("div", {}, go),
  );
}

const STEP_BODIES = { starter: starterStep, capture: captureStep, team: teamStep, review: reviewStep, battle: battleStep };

// ═════════════════════════════════════════════════════════════
//  Piezas de la pantalla
// ═════════════════════════════════════════════════════════════
function track(route) {
  return el(
    "ol",
    { class: "route-track", "aria-label": `Ruta del entrenador: ${route.done} de ${ROUTE.length} pasos hechos` },
    route.steps.map((step, i) =>
      el(
        "li",
        { class: `route-track__step is-${step.state}`, "aria-current": step.state === "current" ? "step" : null },
        el("span", { class: "route-track__dot", "aria-hidden": "true" }, step.state === "done" ? icon("check") : step.state === "locked" ? icon("lock") : String(i + 1)),
        el("span", { class: "route-track__label" }, step.title, el("span", { class: "visually-hidden" }, { done: " (hecho)", current: " (ahora)", locked: " (bloqueado)" }[step.state])),
      ),
    ),
  );
}

async function stepCard(route, progress) {
  const step = route.current;
  const body = await STEP_BODIES[step.id](progress);
  return el(
    "section",
    { class: "panel route-card", id: "route", "aria-labelledby": "route-title" },
    el("p", { class: "route-card__kicker" }, `Paso ${route.done + 1} de ${ROUTE.length}`),
    el("h2", { id: "route-title" }, icon(step.icon), step.title),
    el("p", { class: "muted" }, step.text),
    body,
  );
}

function finishedPanel(collection) {
  const card = (href, iconName, title, text, onClick = null) =>
    el(onClick ? "button" : "a", { class: "shortcut", href: onClick ? null : href, type: onClick ? "button" : null, onClick }, el("span", { class: "shortcut__icon" }, icon(iconName)), el("span", { class: "shortcut__title" }, title), el("span", { class: "muted" }, text));
  return el(
    "section",
    { class: "stack" },
    kantoProgress(collection),
    el(
      "div",
      { class: "grid grid--wide" },
      card("#/buscar", "search", "Seguir coleccionando", "Los 151 de Kanto te esperan."),
      card("#/combate", "bolt", "Combatir", "Sube de nivel y evoluciona."),
      card(null, "flask", "Preguntar a Oak", "Nota del equipo y consejos.", () => openOak()),
    ),
  );
}

function achievements(missions) {
  const done = missions.filter((m) => m.done).length;
  return el(
    "details",
    { class: "panel achievements", id: "missions" },
    el("summary", {}, icon(done === missions.length ? "trophy" : "medal"), `Logros · ${done} de ${missions.length}`),
    el(
      "ol",
      { class: "missions__list" },
      missions.map((m) =>
        el(
          "li",
          { class: `mission ${m.done ? "mission--done" : ""}` },
          el("span", { class: "mission__icon" }, icon(m.done ? "check-circle" : m.icon)),
          el("div", {}, el("strong", {}, m.title, el("span", { class: "visually-hidden" }, m.done ? " (completado)" : " (pendiente)")), m.done ? null : el("p", { class: "muted" }, m.hint)),
          m.done ? null : el("a", { class: "btn btn--secondary btn--small", href: m.href }, "Ir"),
        ),
      ),
    ),
  );
}

// ═════════════════════════════════════════════════════════════
//  Pantalla
// ═════════════════════════════════════════════════════════════
export async function renderHome(view) {
  view.replaceChildren(loader("Preparando tu Pokédex…"));
  const [collection, teams, battles] = await Promise.all([loadCollection(), request("/api/v1/teams"), request("/api/v1/battles")]);
  const flags = missionFlags();
  const usableTeams = teams.filter((t) => t.members.length);
  const wins = battles.filter((b) => b.status === "won").length;
  const losses = battles.filter((b) => b.status === "lost" || b.status === "forfeit").length;
  const route = trainerRoute({ collection, teams: usableTeams.length, flags, wins });
  const progress = { collection, teams: usableTeams, captured: route.captured, losses };
  const name = session.user?.username ?? "entrenador";

  const head = el(
    "div",
    { class: "hero" },
    el(
      "div",
      { class: "hero__text" },
      el("h1", {}, `¡Hola, ${name}!`),
      el("p", { class: "muted" }, route.finished ? "Has completado la ruta del entrenador. ¿Qué quieres hacer hoy?" : "Sigue la ruta del entrenador: un paso cada vez."),
    ),
  );
  const missions = evaluateMissions({ collection, teams: teams.length, flags });
  const main = route.finished ? finishedPanel(collection) : el("div", { class: "stack" }, track(route), await stepCard(route, progress));
  view.replaceChildren(head, main, achievements(missions));
}
