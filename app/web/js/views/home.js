// Inicio: saludo y accesos directos a cada sección. Las ramas siguientes añaden aquí
// las misiones de entrenador y el progreso de la Pokédex.

import { el } from "../dom.js";
import { navRoutes } from "../router.js";
import { session } from "../session.js";

export const homeExtras = [];

export function renderHome(view) {
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
        el("p", { class: "muted" }, "¿Qué quieres hacer hoy?"),
      ),
    ),
    ...homeExtras.map((extra) => extra()),
    el("h2", { class: "visually-hidden" }, "Secciones"),
    el("div", { class: "grid grid--wide" }, shortcuts),
  );
}
