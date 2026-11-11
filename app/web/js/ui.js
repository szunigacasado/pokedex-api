// Piezas de interfaz reutilizables: avisos, carga, ventanas modales, tarjetas e insignias.

import { el } from "./dom.js";
import { artworkUrl, dexNumber, displayName, typeClass, typeLabel } from "./format.js";

// ── Avisos ───────────────────────────────────────────────────
export function toast(message, { type = "info", timeout = 4500 } = {}) {
  const box = document.getElementById("toasts");
  const node = el("div", { class: `toast toast--${type}` }, message);
  box.append(node);
  setTimeout(() => node.remove(), timeout);
  return node;
}

/** Aviso con cuenta atrás, para cuando la API pide esperar (429 + Retry-After). */
export function countdownToast(seconds, prefix = "Vas muy rápido. Puedes volver a intentarlo en") {
  let left = Math.max(1, Math.ceil(seconds));
  const node = toast(`${prefix} ${left} s.`, { type: "warn", timeout: (left + 1) * 1000 });
  const timer = setInterval(() => {
    left -= 1;
    if (left <= 0) {
      clearInterval(timer);
      node.textContent = "¡Listo! Ya puedes seguir.";
      node.className = "toast toast--ok";
      return;
    }
    node.textContent = `${prefix} ${left} s.`;
  }, 1000);
}

/** Muestra el error de una petición de la forma más útil para el usuario. */
export function showError(error) {
  if (error?.name === "AbortError") return;
  if (error?.status === 429 && error.retryAfter) {
    countdownToast(error.retryAfter);
    return;
  }
  toast(error?.message ?? "Algo ha fallado.", { type: "error", timeout: 6000 });
}

// ── Estados de carga y vacío ─────────────────────────────────
export function loader(text = "Cargando…") {
  return el("div", { class: "loader", role: "status" }, el("div", { class: "pokeball", "aria-hidden": "true" }), el("p", {}, text));
}

export function empty(art, title, text, action = null) {
  return el(
    "div",
    { class: "empty" },
    el("div", { class: "empty__art", "aria-hidden": "true" }, art),
    el("h2", {}, title),
    text ? el("p", {}, text) : null,
    action,
  );
}

// ── Tipos y tarjetas ─────────────────────────────────────────
export function typeBadge(type) {
  return el("span", { class: `type type--${type} ${typeClass(type)}` }, typeLabel(type));
}

export function typeBadges(types) {
  return el("div", { class: "types" }, (types ?? []).map(typeBadge));
}

/**
 * Ilustración oficial (se descarga solo al aparecer en pantalla). Si no existe, se usa
 * el sprite pequeño; si tampoco, una silueta de interrogación.
 */
function cardImage(id, sprite) {
  const img = el("img", { class: "card__img", src: artworkUrl(id), alt: "", loading: "lazy", decoding: "async", width: 110, height: 110 });
  img.addEventListener("error", () => {
    if (sprite && img.src !== sprite) img.src = sprite;
    else img.replaceWith(el("div", { class: "card__img card__img--missing", "aria-hidden": "true" }, "?"));
  });
  return img;
}

/**
 * Tarjeta de un Pokémon. `href` la convierte en enlace; `onClick`, en botón.
 * `badge` muestra un icono en la esquina (★ favorito, ● capturado…).
 */
export function pokemonCard({ id, name, sprite, types = [], href, onClick, badge, label }) {
  const tag = href ? "a" : "button";
  const props = {
    class: `card ${typeClass(types[0])}`,
    href,
    type: href ? null : "button",
    onClick,
    "aria-label": label ?? `${displayName(name)}, ${dexNumber(id)}`,
  };
  return el(
    tag,
    props,
    el("span", { class: "card__number" }, dexNumber(id)),
    badge ? el("span", { class: "card__badge", "aria-hidden": "true" }, badge) : null,
    cardImage(id, sprite),
    el("span", { class: "card__name" }, displayName(name)),
    typeBadges(types),
  );
}

// ── Ventanas modales ─────────────────────────────────────────
/**
 * Abre un <dialog> modal con el contenido dado. El navegador gestiona el foco, Escape
 * y el fondo inerte; al cerrar, el foco vuelve a donde estaba.
 */
export function openModal(content, { label = "Detalle", onClose } = {}) {
  const previous = document.activeElement;
  const dialog = el(
    "dialog",
    { class: "modal", "aria-label": label },
    el(
      "button",
      { class: "icon-btn modal__close btn--secondary", type: "button", "aria-label": "Cerrar", onClick: () => dialog.close() },
      "✕",
    ),
    el("div", { class: "modal__body" }, content),
  );
  dialog.addEventListener("close", () => {
    dialog.remove();
    onClose?.();
    if (previous instanceof HTMLElement) previous.focus();
  });
  dialog.addEventListener("click", (event) => {
    if (event.target === dialog) dialog.close();
  });
  document.body.append(dialog);
  dialog.showModal();
  return dialog;
}

/** Botón que muestra «Cargando…» mientras se ejecuta una acción asíncrona. */
export async function busy(button, action, busyText = "Un momento…") {
  const original = button.textContent;
  button.disabled = true;
  button.setAttribute("aria-busy", "true");
  button.textContent = busyText;
  try {
    return await action();
  } finally {
    button.disabled = false;
    button.removeAttribute("aria-busy");
    button.textContent = original;
  }
}
