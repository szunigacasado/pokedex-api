// Modo técnico: enseña en directo cada petición REST que hace la web. La interfaz normal
// no muestra nada técnico; este panel es para explicar qué pasa por debajo.

import { accessTokenSecondsLeft, events, isLoggedIn } from "./api.js";
import { el, prefs } from "./dom.js";
import { icon } from "./icons.js";

const MAX_ENTRIES = 60;

function statusClass(status) {
  return `req__status req__status--${String(status).charAt(0)}`;
}

function entryNode(entry) {
  const meta = [
    el("span", {}, `${entry.ms} ms`),
    entry.requestId ? el("span", { title: "X-Request-ID: búscalo en el log del servidor" }, `id ${entry.requestId.slice(0, 8)}`) : null,
    entry.limit ? el("span", { title: "Cabeceras X-RateLimit-*" }, `cupo ${entry.remaining}/${entry.limit}`) : null,
    entry.renewed ? el("span", {}, icon("refresh"), "tras renovar la sesión") : null,
  ];
  return el(
    "li",
    { class: "req" },
    el(
      "div",
      { class: "req__line" },
      el("span", { class: `req__method req__method--${entry.method}` }, entry.method),
      el("span", { class: "req__path" }, entry.path),
      el("span", { class: statusClass(entry.status) }, entry.status === 0 ? "red" : String(entry.status)),
    ),
    el("div", { class: "req__meta" }, meta),
  );
}

export function initTechPanel() {
  const toggle = document.getElementById("tech-toggle");
  const panel = document.getElementById("tech-panel");
  const list = el("ol", { class: "tech-panel__list", "aria-live": "polite", reversed: true });
  const session = el("p", {});
  let count = 0;

  const refreshSession = () => {
    session.textContent = isLoggedIn()
      ? `Sesión: access token en memoria, caduca en ${Math.round(accessTokenSecondsLeft() / 60)} min. Se renueva solo.`
      : "Sin sesión: las peticiones van sin token.";
  };

  panel.append(
    el(
      "div",
      { class: "tech-panel__head" },
      el("h2", { id: "tech-title" }, icon("gear"), "Modo técnico"),
      el("p", {}, "Cada acción de la web es una petición a la API REST. Aquí ves el método, la ruta, el código de respuesta, lo que tarda, su X-Request-ID y el cupo de rate limiting que te queda."),
      session,
      el(
        "div",
        { class: "row" },
        el("button", { type: "button", class: "btn btn--small btn--secondary", onClick: () => { list.replaceChildren(); count = 0; } }, "Limpiar"),
        el("a", { class: "btn btn--small btn--secondary", href: "/docs", target: "_blank", rel: "noopener" }, "Abrir /docs"),
        el("button", { type: "button", class: "btn btn--small btn--secondary", onClick: () => setOpen(false) }, "Cerrar"),
      ),
    ),
    list,
  );

  events.addEventListener("request", (event) => {
    list.prepend(entryNode(event.detail));
    count += 1;
    if (count > MAX_ENTRIES) list.lastElementChild?.remove();
    refreshSession();
  });
  events.addEventListener("session", refreshSession);

  function setOpen(open) {
    panel.hidden = !open;
    toggle.setAttribute("aria-pressed", String(open));
    document.body.classList.toggle("has-tech-panel", open);
    prefs.set("techPanel", open);
    refreshSession();
  }

  toggle.prepend(icon("gear"));
  toggle.addEventListener("click", () => setOpen(panel.hidden));
  setOpen(prefs.get("techPanel", false));
}
