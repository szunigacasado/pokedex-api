// Inicio: saludo, progreso, misiones de entrenador y accesos a cada sección.

import { request } from "../api.js";
import { el } from "../dom.js";
import { loadCollection } from "../data.js";
import { evaluateMissions, missionFlags } from "../missions.js";
import { navRoutes } from "../router.js";
import { session } from "../session.js";
import { startTour, tourSeen } from "../tour.js";
import { loader } from "../ui.js";
import { kantoProgress } from "./collection.js";

function missionsPanel(missions) {
  const done = missions.filter((m) => m.done).length;
  const all = done === missions.length;
  return el(
    "section",
    { class: "panel stack missions", id: "missions", "aria-labelledby": "missions-title" },
    el(
      "div",
      { class: "row row--between" },
      el("h2", { id: "missions-title" }, all ? "🏆 ¡Eres un Maestro Pokédex!" : "🎯 Misiones de entrenador"),
      el("span", { class: "missions__count" }, `${done} de ${missions.length}`),
    ),
    el("progress", { class: "progress", max: missions.length, value: done, "aria-label": "Misiones completadas" }),
    el(
      "ol",
      { class: "missions__list" },
      missions.map((m) =>
        el(
          "li",
          { class: `mission ${m.done ? "mission--done" : ""}` },
          el("span", { class: "mission__icon", "aria-hidden": "true" }, m.done ? "✅" : m.icon),
          el(
            "div",
            {},
            el("strong", {}, m.title, el("span", { class: "visually-hidden" }, m.done ? " (completada)" : " (pendiente)")),
            m.done ? null : el("p", { class: "muted" }, m.hint),
          ),
          m.done ? null : el("a", { class: "btn btn--secondary btn--small", href: m.href }, "Ir"),
        ),
      ),
    ),
  );
}

export async function renderHome(view) {
  view.replaceChildren(loader("Preparando tu Pokédex…"));
  const [collection, teams] = await Promise.all([loadCollection(), request("/api/v1/teams")]);
  const missions = evaluateMissions({ collection, teams: teams.length, flags: missionFlags() });
  const user = session.user;

  const shortcuts = navRoutes()
    .filter((r) => r.pattern !== "/")
    .map((r) =>
      el(
        "a",
        { class: "shortcut", href: `#${r.pattern}` },
        el("span", { class: "shortcut__icon", "aria-hidden": "true" }, r.nav.icon),
        el("span", { class: "shortcut__title" }, r.nav.label),
        el("span", { class: "muted" }, r.nav.hint ?? ""),
      ),
    );

  view.replaceChildren(
    el(
      "div",
      { class: "hero" },
      el(
        "div",
        { class: "hero__text" },
        el("h1", {}, `¡Hola, ${user?.username ?? "entrenador"}!`),
        el("p", { class: "muted" }, collection.length ? "¿Qué quieres hacer hoy?" : "Tu aventura empieza aquí. Sigue las misiones de abajo."),
      ),
      el("button", { type: "button", class: "btn btn--secondary", onClick: () => startTour() }, "❓ ¿Cómo funciona?"),
    ),
    collection.length ? kantoProgress(collection) : null,
    missionsPanel(missions),
    el("h2", {}, "Secciones"),
    el("div", { class: "grid grid--wide" }, shortcuts),
  );

  // Quien acaba de crear la cuenta recibe el tour sin tener que pedirlo.
  if (session.isNew || !tourSeen()) {
    session.isNew = false;
    requestAnimationFrame(() => startTour());
  }
}
