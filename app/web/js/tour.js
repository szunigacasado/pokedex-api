// Tour: resalta cada parte de la interfaz y explica para qué sirve. Se abre con
// «Más → ¿Cómo funciona?»; a quien empieza ya le guía la ruta del entrenador del inicio.

import { el, prefs } from "./dom.js";

export const STEPS = [
  {
    title: "¡Bienvenido a tu Pokédex!",
    text: "Te enseño dónde está cada cosa en unos pocos pasos. Puedes salir cuando quieras con «Saltar» o la tecla Escape.",
  },
  { target: '.nav__link[href="#/"]', title: "1 · Tu ruta", text: "En el inicio, la ruta del entrenador te dice qué hacer en cada momento: un paso cada vez." },
  { target: '.nav__link[href="#/pokedex"]', title: "2 · Tu colección", text: "Los Pokémon que capturas vienen aquí. Ponles apodo, márcalos como favoritos o descárgalos en PDF." },
  { target: '.nav__link[href="#/equipos"]', title: "3 · Tus equipos", text: "Junta hasta seis y descubre sus puntos fuertes y débiles." },
  { target: '.nav__link[href="#/combate"]', title: "4 · Combate", text: "Contra la máquina o contra un amigo. Ganando, tus Pokémon suben de nivel y evolucionan." },
  { target: "#nav-more", title: "5 · Más", text: "El buscador de los 151, el juego de la silueta, tus estadísticas y la página del Profesor Oak." },
  { target: ".oak-fab", title: "6 · El Profesor Oak", text: "Pídele la nota de tu equipo o recomendaciones desde cualquier pantalla." },
  { target: "#tech-toggle", title: "7 · Modo técnico", text: "¿Curiosidad? Enciéndelo y verás cada petición que la web hace a la API REST, en directo." },
];

const MARGIN = 8;

export function tourSeen() {
  return prefs.get("tourDone", false);
}

export function startTour(steps = STEPS) {
  if (document.querySelector(".tour")) return;
  const previousFocus = document.activeElement;
  let index = 0;

  const spot = el("div", { class: "tour__spot", "aria-hidden": "true" });
  const counter = el("p", { class: "tour__counter" });
  const title = el("h2", { id: "tour-title" });
  const text = el("p", { id: "tour-text" });
  const back = el("button", { type: "button", class: "btn btn--secondary btn--small" }, "Anterior");
  const next = el("button", { type: "button", class: "btn btn--primary btn--small" });
  const skip = el("button", { type: "button", class: "btn btn--ghost btn--small" }, "Saltar");
  const box = el(
    "div",
    { class: "tour__box", role: "dialog", "aria-modal": "true", "aria-labelledby": "tour-title", "aria-describedby": "tour-text", tabindex: "-1" },
    counter,
    title,
    text,
    el("div", { class: "row row--between" }, skip, el("div", { class: "row" }, back, next)),
  );
  const overlay = el("div", { class: "tour" }, spot, box);

  function visibleSteps() {
    // Si una pieza no está en esta pantalla (por ejemplo, las misiones), se salta.
    return steps.filter((s) => !s.target || document.querySelector(s.target));
  }

  function place() {
    const list = visibleSteps();
    const step = list[index];
    const target = step.target ? document.querySelector(step.target) : null;
    counter.textContent = `Paso ${index + 1} de ${list.length}`;
    title.textContent = step.title;
    text.textContent = step.text;
    back.disabled = index === 0;
    next.textContent = index === list.length - 1 ? "¡A jugar!" : "Siguiente";

    if (!target) {
      spot.hidden = true;
      box.classList.add("tour__box--center");
      box.style.removeProperty("top");
      box.style.removeProperty("left");
      return;
    }
    target.scrollIntoView({ block: "center", behavior: "instant" });
    const rect = target.getBoundingClientRect();
    spot.hidden = false;
    spot.style.setProperty("top", `${rect.top - MARGIN}px`);
    spot.style.setProperty("left", `${rect.left - MARGIN}px`);
    spot.style.setProperty("width", `${rect.width + MARGIN * 2}px`);
    spot.style.setProperty("height", `${rect.height + MARGIN * 2}px`);

    box.classList.remove("tour__box--center");
    const boxWidth = Math.min(360, window.innerWidth - 32);
    const below = rect.bottom + 16 + 220 < window.innerHeight;
    const top = below ? rect.bottom + 16 : Math.max(16, rect.top - 16 - box.offsetHeight);
    const left = Math.min(Math.max(16, rect.left), window.innerWidth - boxWidth - 16);
    box.style.setProperty("top", `${top}px`);
    box.style.setProperty("left", `${left}px`);
  }

  function go(delta) {
    const list = visibleSteps();
    if (index + delta >= list.length) return end();
    index = Math.max(0, index + delta);
    place();
    box.focus();
  }

  function end() {
    overlay.remove();
    window.removeEventListener("resize", place);
    document.removeEventListener("keydown", onKey, true);
    prefs.set("tourDone", true);
    if (previousFocus instanceof HTMLElement) previousFocus.focus();
  }

  function onKey(event) {
    if (event.key === "Escape") {
      event.preventDefault();
      end();
    } else if (event.key === "ArrowRight") go(1);
    else if (event.key === "ArrowLeft") go(-1);
    else if (event.key === "Tab") {
      // El foco no sale del cuadro mientras el tour está abierto.
      const focusable = [...box.querySelectorAll("button:not(:disabled)")];
      const first = focusable[0];
      const last = focusable.at(-1);
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    }
  }

  next.addEventListener("click", () => go(1));
  back.addEventListener("click", () => go(-1));
  skip.addEventListener("click", end);
  window.addEventListener("resize", place);
  document.addEventListener("keydown", onKey, true);
  document.body.append(overlay);
  place();
  box.focus();
}
