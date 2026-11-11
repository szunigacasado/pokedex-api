// Asistente Profesor Oak: un chat que usa la API por ti.
//
// - Está en todas las pantallas (botón flotante y panel lateral) y a pantalla completa en
//   #/oak. La conversación es la misma en los dos sitios y vive solo en memoria.
// - Oak sabe qué estás viendo: en un equipo, «este equipo» es ese; en una ficha, ese Pokémon.
// - Lo que Oak consulta llega como tarjetas (nota del equipo, Pokémon) y lo que propone,
//   como botones. Nada cambia hasta que el entrenador pulsa uno: entonces la web llama a la
//   API normal, con sus comprobaciones.

import { events, isLoggedIn, request } from "./api.js";
import { el } from "./dom.js";
import { artworkUrl, displayName } from "./format.js";
import { addToCollection } from "./data.js";
import { icon } from "./icons.js";
import { markDone } from "./missions.js";
import { currentPath, navigate } from "./router.js";
import { sfx } from "./sound.js";
import { showError, toast, typeBadges } from "./ui.js";

const MAX_MESSAGE = 500; // lo mismo que acepta la API
const MAX_HISTORY = 12;
const TOOL_LABELS = {
  my_collection: "tu colección",
  review_team: "la nota del equipo",
  recommend_pokemon: "recomendaciones",
  search_pokemon: "el buscador",
  pokemon_info: "una ficha",
  propose_action: "propuestas",
};
const reducedMotion = () => window.matchMedia("(prefers-reduced-motion: reduce)").matches;

// ═════════════════════════════════════════════════════════════
//  Conversación (compartida por el panel y la página)
// ═════════════════════════════════════════════════════════════
const conversation = { entries: [], pending: false };
const listeners = new Set();

function emit() {
  for (const listener of [...listeners]) listener();
}

/** Lo que el entrenador está viendo, para que «este equipo» tenga sentido. */
export function currentContext() {
  const path = currentPath();
  const team = /^\/equipos\/(\d+)/.exec(path);
  const pokemon = /^\/pokemon\/(\d+)/.exec(path);
  return { team_id: team ? Number(team[1]) : null, pokemon_id: pokemon ? Number(pokemon[1]) : null };
}

/** Envía un mensaje a Oak. `context` sustituye al de la pantalla actual. */
export async function askOak(text, context = currentContext()) {
  const content = text.trim().slice(0, MAX_MESSAGE);
  if (!content || conversation.pending) return false;
  conversation.entries.push({ role: "user", content });
  conversation.pending = true;
  emit();
  try {
    const messages = conversation.entries.slice(-MAX_HISTORY).map(({ role, content: c }) => ({ role, content: c.slice(0, MAX_MESSAGE) }));
    while (messages[0]?.role === "assistant") messages.shift();
    const reply = await request("/api/v1/oak/chat", { method: "POST", json: { messages, context } });
    conversation.entries.push({ role: "assistant", content: reply.reply, reply, fresh: true, done: new Set() });
    markDone("ask");
    if (reply.tools.includes("review_team")) markDone("advice");
    return true;
  } catch (error) {
    conversation.entries.pop(); // el mensaje vuelve a la caja de texto
    showError(error);
    return false;
  } finally {
    conversation.pending = false;
    emit();
  }
}

export function clearConversation() {
  conversation.entries = [];
  emit();
}

// ═════════════════════════════════════════════════════════════
//  Tarjetas
// ═════════════════════════════════════════════════════════════
const decimal = (n) => String(n).replace(".", ",");

/** Nota del equipo: anillo con la nota, desglose y puntos fuertes y débiles. */
export function reviewCard(review, title = null) {
  const ring = el(
    "div",
    { class: "grade", role: "img", "aria-label": `Nota ${decimal(review.score)} de 10: ${review.grade}`, style: { "--p": String(review.score * 10) } },
    el("span", { class: "grade__score" }, decimal(review.score)),
    el("span", { class: "grade__max" }, "/10"),
  );
  return el(
    "article",
    { class: "oak-card review" },
    title ? el("h3", { class: "oak-card__title" }, title) : null,
    el("div", { class: "review__head" }, ring, el("div", {}, el("p", { class: "review__grade" }, review.grade), el("p", { class: "muted" }, "Nota calculada por la API con la tabla de tipos."))),
    el(
      "ul",
      { class: "review__parts" },
      review.parts.map((part) =>
        el(
          "li",
          {},
          el("div", { class: "row row--between" }, el("strong", {}, part.label), el("span", {}, `${decimal(part.score)} / ${decimal(part.max)}`)),
          el("span", { class: "meter", "aria-hidden": "true" }, el("span", { class: "meter__fill", style: { "--w": `${(part.score / part.max) * 100}%` } })),
          el("p", { class: "muted" }, part.detail),
        ),
      ),
    ),
    review.strengths.length ? el("ul", { class: "review__notes" }, review.strengths.map((s) => el("li", { class: "is-good" }, icon("check"), s))) : null,
    review.weaknesses.length ? el("ul", { class: "review__notes" }, review.weaknesses.map((s) => el("li", { class: "is-bad" }, icon("warning"), s))) : null,
  );
}

function pokemonCard(card) {
  return el(
    "article",
    { class: "oak-card" },
    el("h3", { class: "oak-card__title" }, card.title),
    el(
      "ul",
      { class: "oak-mons" },
      card.pokemon.map((p) =>
        el(
          "li",
          {},
          el(
            "a",
            { class: "oak-mon", href: `#/pokemon/${p.id}` },
            el("img", { src: artworkUrl(p.id), alt: "", width: 56, height: 56, loading: "lazy" }),
            el("span", { class: "oak-mon__body" }, el("strong", {}, displayName(p.name)), typeBadges(p.types), p.reason ? el("small", { class: "muted" }, p.reason) : null),
            p.in_pokedex ? el("span", { class: "chip chip--ok" }, icon("check"), "La tienes") : null,
          ),
        ),
      ),
    ),
  );
}

// ═════════════════════════════════════════════════════════════
//  Acciones propuestas: solo se hacen si el entrenador las pulsa
// ═════════════════════════════════════════════════════════════
async function runAction(action) {
  if (action.kind === "open_team") {
    navigate(`/equipos/${action.team_id}`);
    return null;
  }
  if (action.kind === "open_pokemon") {
    navigate(`/pokemon/${action.pokemon_id}`);
    return null;
  }
  if (action.kind === "capture") {
    const entry = await addToCollection(action.pokemon_id);
    sfx("capture");
    return `¡${displayName(entry.pokemon_name)} está en tu Pokédex!`;
  }
  if (action.kind === "add_to_team" || action.kind === "replace_in_team") {
    const team = await request(`/api/v1/teams/${action.team_id}`);
    const current = team.members.map((m) => m.pokemon_id);
    const ids = action.kind === "add_to_team" ? [...current, action.pokemon_id] : current.map((id) => (id === action.replace_id ? action.pokemon_id : id));
    await request(`/api/v1/teams/${team.id}`, { method: "PUT", json: { pokemon_ids: ids } });
    sfx("success");
    if (currentPath() === `/equipos/${team.id}`) navigate(currentPath()); // repinta el equipo
    return action.kind === "add_to_team" ? `Añadido a «${team.name}».` : `«${team.name}» actualizado.`;
  }
  return null;
}

function actionButtons(entry) {
  return el(
    "div",
    { class: "oak-actions", role: "group", "aria-label": "Lo que te propone Oak" },
    entry.reply.actions.map((action, index) => {
      if (entry.done.has(index)) return el("span", { class: "chip chip--ok" }, icon("check"), "Hecho");
      const opens = action.kind.startsWith("open_");
      const button = el("button", { type: "button", class: `btn btn--small ${opens ? "btn--secondary" : "btn--primary"}` }, icon(opens ? "arrow-right" : { capture: "pokeball", replace_in_team: "refresh" }[action.kind] ?? "plus"), action.label);
      button.addEventListener("click", async () => {
        button.disabled = true;
        try {
          const message = await runAction(action);
          if (message) {
            entry.done.add(index);
            toast(message, { type: "ok" });
            emit();
          }
        } catch (error) {
          showError(error);
        } finally {
          button.disabled = false;
        }
      });
      return button;
    }),
  );
}

// ═════════════════════════════════════════════════════════════
//  Chat
// ═════════════════════════════════════════════════════════════
function typewriter(node, text) {
  if (reducedMotion()) {
    node.textContent = text;
    return;
  }
  node.textContent = "";
  let i = 0;
  const timer = setInterval(() => {
    i += 3;
    node.textContent = text.slice(0, i);
    if (i >= text.length || !node.isConnected) {
      node.textContent = text;
      clearInterval(timer);
    }
  }, 16);
}

function oakMessage(entry) {
  const text = el("p", { class: "msg__text" });
  if (entry.fresh) {
    entry.fresh = false;
    typewriter(text, entry.content);
  } else {
    text.textContent = entry.content;
  }
  const { reply } = entry;
  const consulted = reply.tools.filter((t) => t !== "propose_action").map((t) => TOOL_LABELS[t] ?? t);
  const note = [reply.source === "ai" ? "Con IA" : "Sin IA (reglas)", consulted.length ? `ha consultado ${[...new Set(consulted)].join(", ")}` : null].filter(Boolean).join(" · ");
  return el(
    "li",
    { class: "msg msg--oak" },
    el("div", { class: "msg__avatar", "aria-hidden": "true" }, icon("flask")),
    el(
      "div",
      { class: "msg__body" },
      el("p", { class: "visually-hidden" }, "Profesor Oak dice:"),
      text,
      reply.cards.map((card) => (card.kind === "review" ? reviewCard(card.review, card.title) : pokemonCard(card))),
      reply.actions.length ? actionButtons(entry) : null,
      el("p", { class: "msg__note" }, icon(reply.source === "ai" ? "sparkles" : "rules"), note),
    ),
  );
}

function suggestions() {
  const { team_id: team, pokemon_id: pokemon } = currentContext();
  const list = team
    ? ["Revisa este equipo y ponle nota", "¿Qué Pokémon le falta a este equipo?"]
    : ["Revisa mi equipo y ponle nota", "¿Qué Pokémon le falta a mi equipo?"];
  if (pokemon) list.push("¿Qué tal es este Pokémon?");
  list.push("Los 3 de agua más rápidos", "¿Qué tengo en mi Pokédex?");
  return list;
}

/**
 * Crea la vista del chat. `compact` para el panel lateral.
 * Devuelve el elemento; se actualiza solo mientras esté en la página.
 */
export function createChat({ compact = false } = {}) {
  const log = el("ol", { class: "oak-chat__log", role: "log", "aria-live": "polite", "aria-label": "Conversación con el Profesor Oak" });
  const input = el("textarea", { class: "input oak-chat__input", rows: 1, maxlength: MAX_MESSAGE, placeholder: "Pregúntale a Oak…", "aria-label": "Tu mensaje para el Profesor Oak" });
  const send = el("button", { type: "submit", class: "btn btn--primary", "aria-label": "Enviar" }, icon("arrow-right"));
  const form = el("form", { class: "oak-chat__form" }, input, send);
  const chips = el("div", { class: "oak-chat__chips", role: "group", "aria-label": "Sugerencias" });
  const root = el("section", { class: `oak-chat${compact ? " oak-chat--compact" : ""}` }, log, chips, form);

  async function submit(text) {
    const value = text ?? input.value;
    if (!value.trim()) return;
    input.value = "";
    const ok = await askOak(value);
    if (!ok && !input.value) input.value = value.trim();
    input.focus();
  }

  form.addEventListener("submit", (event) => {
    event.preventDefault();
    submit();
  });
  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      submit();
    }
  });

  function draw() {
    const items = conversation.entries.map((entry) =>
      entry.role === "user" ? el("li", { class: "msg msg--you" }, el("p", { class: "visually-hidden" }, "Tú:"), el("p", { class: "msg__text" }, entry.content)) : oakMessage(entry),
    );
    if (!items.length) {
      items.push(
        el(
          "li",
          { class: "msg msg--oak" },
          el("div", { class: "msg__avatar", "aria-hidden": "true" }, icon("flask")),
          el("div", { class: "msg__body" }, el("p", { class: "msg__text" }, "¡Hola! Soy el Profesor Oak. Puedo ponerle nota a tu equipo, decirte qué Pokémon le faltan y buscar por ti. Tú decides si haces lo que te propongo.")),
        ),
      );
    }
    if (conversation.pending) items.push(el("li", { class: "msg msg--oak msg--thinking" }, el("div", { class: "msg__avatar", "aria-hidden": "true" }, icon("flask")), el("p", { class: "msg__text" }, el("span", { class: "dots", "aria-hidden": "true" }, el("span"), el("span"), el("span")), "Oak está consultando…")));
    log.replaceChildren(...items);
    log.scrollTop = log.scrollHeight;
    chips.replaceChildren(...(conversation.entries.length ? [] : suggestions().map((s) => el("button", { type: "button", class: "chip chip--button", onClick: () => submit(s) }, s))));
    send.disabled = conversation.pending;
  }

  const listener = () => (root.isConnected ? draw() : listeners.delete(listener));
  listeners.add(listener);
  draw();
  return root;
}

// ═════════════════════════════════════════════════════════════
//  Botón flotante y panel lateral (todas las pantallas)
// ═════════════════════════════════════════════════════════════
export function initOakPanel() {
  const fab = el("button", { type: "button", class: "oak-fab", "aria-expanded": "false", "aria-controls": "oak-drawer", hidden: true }, icon("flask"), el("span", {}, "Oak"));
  const close = el("button", { type: "button", class: "icon-btn", "aria-label": "Cerrar el chat de Oak" }, icon("x"));
  const body = el("div", { class: "oak-drawer__body" });
  const drawer = el(
    "aside",
    { class: "oak-drawer", id: "oak-drawer", "aria-labelledby": "oak-drawer-title", hidden: true },
    el("header", { class: "oak-drawer__head" }, el("h2", { id: "oak-drawer-title" }, icon("flask"), "Profesor Oak"), el("a", { class: "btn btn--ghost btn--small", href: "#/oak" }, "Ampliar"), close),
    body,
  );
  document.body.append(fab, drawer);

  function setOpen(open) {
    drawer.hidden = !open;
    fab.setAttribute("aria-expanded", String(open));
    if (open) {
      body.replaceChildren(createChat({ compact: true }));
      body.querySelector("textarea")?.focus();
    } else {
      body.replaceChildren();
    }
  }

  function sync() {
    const visible = isLoggedIn() && currentPath() !== "/oak";
    fab.hidden = !visible;
    if (!visible && !drawer.hidden) setOpen(false);
  }

  fab.addEventListener("click", () => setOpen(drawer.hidden));
  close.addEventListener("click", () => {
    setOpen(false);
    fab.focus();
  });
  drawer.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      setOpen(false);
      fab.focus();
    }
  });
  window.addEventListener("hashchange", sync);
  events.addEventListener("session", (event) => {
    if (!event.detail.loggedIn) clearConversation();
    sync();
  });
  return sync;
}
