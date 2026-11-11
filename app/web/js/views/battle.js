// Combates: preparación (equipo, dificultad, tipo del rival), arena animada turno a turno
// y resultado con experiencia, subidas de nivel y evoluciones.
// Toda la lógica la resuelve la API; aquí solo se anima lo que devuelve.
//
// La misma arena sirve para dos rivales:
// - la máquina, por HTTP (POST /battles/{id}/turn), y
// - un amigo, por WebSocket (/api/v1/pvp/ws): el servidor espera las dos acciones, resuelve
//   el turno y se lo cuenta a cada uno desde su lado. Ningún navegador decide el resultado.

import { request, socketToken } from "../api.js";
import { el, prefs, sleep } from "../dom.js";
import { TYPES, artworkUrl, backSpriteUrl, displayName, multiplierLabel, spriteUrl, typeClass, typeLabel } from "../format.js";
import { loadCollection, loadProgress, replaceEntry } from "../data.js";
import { icon } from "../icons.js";
import { markDone } from "../missions.js";
import { navigate } from "../router.js";
import { playCry, sfx } from "../sound.js";
import { busy, empty, loader, showError, toast } from "../ui.js";

const DIFFICULTIES = [
  ["easy", "Fácil"],
  ["normal", "Normal"],
  ["hard", "Difícil"],
];
const STATUS_TEXT = { won: "Victoria", lost: "Derrota", draw: "Empate", forfeit: "Abandonado", active: "En curso" };
const CODE_PATTERN = /^[A-HJ-NP-Z2-9]{6}$/; // el alfabeto de los códigos: sin 0/O ni 1/I
const reducedMotion = () => window.matchMedia("(prefers-reduced-motion: reduce)").matches;

// Qué hacer al abrir #/combate/amigo/CÓDIGO desde el lobby: null = el reto es mío;
// un número = me uno con ese equipo. Sin intención (un enlace compartido) se pregunta.
const intents = new Map();

// ═════════════════════════════════════════════════════════════
//  Preparación
// ═════════════════════════════════════════════════════════════
function usableTeams(teams) {
  return teams.filter((t) => t.members.length);
}

function preferredTeam(usable) {
  const last = prefs.get("battleTeam", null);
  return usable.some((t) => t.id === last) ? last : usable[0].id;
}

/** Botones para elegir equipo. Guarda la elección en `state.team` y la recuerda. */
function teamPicker(usable, state) {
  const buttons = usable.map((team) => {
    const button = el(
      "button",
      { type: "button", class: "team-pick", "aria-pressed": String(team.id === state.team) },
      el("span", { class: "team-pick__name" }, team.name),
      el("span", { class: "team-pick__members", "aria-hidden": "true" }, team.members.map((m) => el("img", { src: artworkUrl(m.pokemon_id), alt: "", width: 32, height: 32 }))),
    );
    button.addEventListener("click", () => {
      state.team = team.id;
      prefs.set("battleTeam", team.id);
      for (const b of buttons) b.setAttribute("aria-pressed", String(b === button));
    });
    return button;
  });
  return el("div", { class: "team-picks", role: "group", "aria-label": "Tu equipo" }, buttons);
}

function needTeam() {
  return empty("swords", "Necesitas un equipo", "Crea un equipo con tus Pokémon y vuelve para combatir.", el("a", { class: "btn btn--primary", href: "#/equipos/nuevo" }, icon("plus"), "Crear un equipo"));
}

export async function renderBattleLobby(view) {
  view.replaceChildren(loader("Preparando el estadio…"));
  const [teams, history] = await Promise.all([request("/api/v1/teams"), request("/api/v1/battles")]);
  const usable = usableTeams(teams);
  if (!usable.length) {
    view.replaceChildren(el("h1", {}, "Combate"), needTeam());
    return;
  }

  const state = { team: preferredTeam(usable), difficulty: prefs.get("battleDifficulty", "normal") };
  const difficultyButtons = DIFFICULTIES.map(([value, label]) => {
    const button = el("button", { type: "button", "aria-pressed": String(value === state.difficulty) }, label);
    button.addEventListener("click", () => {
      state.difficulty = value;
      prefs.set("battleDifficulty", value);
      for (const b of difficultyButtons) b.setAttribute("aria-pressed", String(b === button));
    });
    return button;
  });
  const theme = el("select", { class: "select", id: "battle-theme" }, el("option", { value: "" }, "Variado"), Object.entries(TYPES).map(([value, label]) => el("option", { value }, label)));
  const go = el("button", { type: "button", class: "btn btn--primary btn--big" }, icon("bolt"), "¡A combatir!");
  go.addEventListener("click", () =>
    busy(go, async () => {
      try {
        const battle = await request("/api/v1/battles", {
          method: "POST",
          json: { team_id: state.team, difficulty: state.difficulty, theme: theme.value || null },
        });
        sfx("click");
        navigate(`/combate/${battle.id}`);
      } catch (error) {
        showError(error);
      }
    }, "Buscando rival…"),
  );

  // ── Contra un amigo ───────────────────────────────────────
  const challenge = el("button", { type: "button", class: "btn btn--primary" }, icon("users"), "Crear un reto");
  challenge.addEventListener("click", () =>
    busy(challenge, async () => {
      try {
        const room = await request("/api/v1/pvp/rooms", { method: "POST", json: { team_id: state.team } });
        intents.set(room.code, null);
        sfx("click");
        navigate(`/combate/amigo/${room.code}`);
      } catch (error) {
        showError(error);
      }
    }, "Creando el reto…"),
  );
  const codeInput = el("input", {
    class: "input input--code",
    id: "friend-code",
    name: "code",
    maxlength: 6,
    autocomplete: "off",
    autocapitalize: "characters",
    spellcheck: "false",
    placeholder: "ABC234",
    "aria-describedby": "friend-code-hint",
  });
  codeInput.addEventListener("input", () => {
    codeInput.value = codeInput.value.toUpperCase().replace(/[^A-Z0-9]/g, "");
    codeInput.removeAttribute("aria-invalid");
  });
  const joinForm = el(
    "form",
    { class: "stack stack--tight", novalidate: true },
    el("label", { for: "friend-code" }, "¿Te han pasado un código?"),
    el("div", { class: "input-row" }, codeInput, el("button", { type: "submit", class: "btn btn--secondary" }, icon("arrow-right"), "Unirse")),
    el("p", { class: "muted", id: "friend-code-hint" }, "Seis letras y números. Combatirás con el equipo elegido arriba."),
  );
  joinForm.addEventListener("submit", (event) => {
    event.preventDefault();
    const code = codeInput.value.trim().toUpperCase();
    if (!CODE_PATTERN.test(code)) {
      codeInput.setAttribute("aria-invalid", "true");
      toast("El código tiene 6 caracteres: letras y números.", { type: "error" });
      codeInput.focus();
      return;
    }
    intents.set(code, state.team);
    navigate(`/combate/amigo/${code}`);
  });

  view.replaceChildren(
    el("div", { class: "page-head" }, el("div", {}, el("h1", {}, "Combate"), el("p", {}, "Tu equipo contra la máquina o contra un amigo. Ganando, tus Pokémon suben de nivel y pueden evolucionar."))),
    el(
      "section",
      { class: "panel stack" },
      el("h2", {}, "1 · Elige tu equipo"),
      teamPicker(usable, state),
      el("h2", {}, "2 · Elige el rival"),
      el("div", { class: "row" }, el("div", { class: "segmented segmented--3", role: "group", "aria-label": "Dificultad" }, difficultyButtons), el("div", { class: "field field--inline" }, el("label", { for: "battle-theme" }, "Tipo del rival"), theme)),
      el("div", {}, go),
    ),
    el(
      "section",
      { class: "panel stack friend-panel" },
      el("h2", { class: "row" }, icon("users"), "Contra un amigo"),
      el("p", { class: "muted" }, "En directo, cada uno desde su dispositivo y con su cuenta. Los dos ganáis experiencia."),
      el("div", { class: "friend-panel__grid" }, el("div", { class: "stack stack--tight" }, el("p", {}, "Crea un reto con el equipo elegido y pásale el enlace o el código."), el("div", {}, challenge)), joinForm),
    ),
    history.length
      ? el(
          "section",
          { class: "stack" },
          el("h2", {}, "Tus últimos combates"),
          el(
            "ul",
            { class: "history" },
            history.map((b) =>
              el(
                "li",
                {},
                el(
                  "a",
                  { href: `#/combate/${b.id}` },
                  el("span", { class: `result result--${b.status}` }, STATUS_TEXT[b.status] ?? b.status),
                  b.mode === "friend" ? el("span", { class: "history__friend", title: "Contra un amigo" }, icon("users")) : null,
                  b.opponent_name,
                ),
              ),
            ),
          ),
        )
      : null,
  );
}

// ═════════════════════════════════════════════════════════════
//  Arena (común a la máquina y al amigo)
// ═════════════════════════════════════════════════════════════
function hpClass(hp, max) {
  const ratio = hp / max;
  return ratio > 0.5 ? "hp--ok" : ratio > 0.2 ? "hp--warn" : "hp--danger";
}

function effectivenessHint(value) {
  if (value === 0) return "No le afecta";
  if (value >= 2) return `Muy eficaz (${multiplierLabel(value)})`;
  if (value < 1) return `Poco eficaz (${multiplierLabel(value)})`;
  return "Normal";
}

/**
 * Monta la arena en `view` y devuelve sus controles.
 * @param {object} initial  Estado con la forma de BattleView (you, rival, actions, status…).
 * @param {object} options
 *   onAction(action)  Envía la acción elegida. La respuesta llega con `update(next)`.
 *   onForfeit()       Rendirse.
 *   intro             Frase de entrada en el turno 0.
 */
function createArena(view, initial, { onAction, onForfeit, intro }) {
  let state = initial;
  let speed = prefs.get("battleFast", false) ? 0.45 : 1;
  let queue = Promise.resolve();

  // ── Elementos ─────────────────────────────────────────────
  const make = (who) => {
    const sprite = el("img", { class: `fighter__sprite fighter__sprite--${who}`, alt: "", width: 192, height: 192 });
    const name = el("span", { class: "fighter__name" });
    const level = el("span", { class: "fighter__level" });
    const bar = el("span", { class: "hp__fill" });
    const numbers = el("span", { class: "hp__numbers" });
    const box = el("div", { class: `fighter__box fighter__box--${who}` }, el("div", { class: "row row--between" }, name, level), el("div", { class: "hp", role: "img" }, bar), who === "you" ? numbers : null);
    const balls = el("div", { class: "fighter__balls", "aria-hidden": "true" });
    return { sprite, name, level, bar, numbers, box, balls, root: el("div", { class: `fighter fighter--${who}` }, box, el("div", { class: "fighter__stage" }, sprite), balls) };
  };
  const ui = { you: make("you"), rival: make("rival") };
  const message = el("p", { class: "battle-msg__text", "aria-live": "polite" });
  const status = el("p", { class: "battle-msg__status", "aria-live": "polite" });
  const controls = el("div", { class: "battle-controls" });
  const fast = el("button", { type: "button", class: "btn btn--secondary btn--small", "aria-pressed": String(speed < 1) }, icon("chevron-right"), "Rápido");
  fast.addEventListener("click", () => {
    speed = speed < 1 ? 1 : 0.45;
    prefs.set("battleFast", speed < 1);
    fast.setAttribute("aria-pressed", String(speed < 1));
  });
  const forfeitButton = el("button", { type: "button", class: "btn btn--ghost btn--small danger" }, icon("x"), "Rendirse");

  const fighterOf = (side, index) => (side === 0 ? state.you : state.rival).fighters[index];
  const label = (f) => f.nickname ?? displayName(f.name);

  function paint(who, fighter) {
    const parts = ui[who];
    parts.sprite.src = who === "you" ? backSpriteUrl(fighter.pokemon_id) : spriteUrl(fighter.pokemon_id);
    parts.sprite.classList.toggle("is-fainted", !fighter.alive);
    parts.name.textContent = label(fighter);
    parts.level.textContent = `Nv. ${fighter.level}`;
    setHp(who, fighter.hp, fighter.max_hp);
  }

  function setHp(who, hp, max) {
    const parts = ui[who];
    parts.bar.style.setProperty("--w", `${Math.max(0, (hp / max) * 100)}%`);
    parts.bar.className = `hp__fill ${hpClass(hp, max)}`;
    parts.numbers.textContent = `${hp} / ${max} PS`;
    parts.box.querySelector(".hp").setAttribute("aria-label", `${hp} de ${max} PS`);
  }

  function paintBalls() {
    for (const [who, side] of [["you", state.you], ["rival", state.rival]]) {
      ui[who].balls.replaceChildren(...side.fighters.map((f) => icon("pokeball", { fill: f.alive })));
    }
  }

  function paintAll() {
    paint("you", state.you.fighters[state.you.active]);
    paint("rival", state.rival.fighters[state.rival.active]);
    paintBalls();
  }

  async function say(text, ms = 900) {
    message.textContent = text;
    await sleep(ms * speed);
  }

  async function flash(who, cls, ms) {
    ui[who].sprite.classList.add(cls);
    await sleep((reducedMotion() ? 50 : ms) * speed);
    ui[who].sprite.classList.remove(cls);
  }

  // ── Reproducir los sucesos de un turno ────────────────────
  async function animate(events) {
    for (const event of events) {
      const who = event.side === 0 ? "you" : "rival";
      if (event.type === "switch" || event.type === "send") {
        const fighter = fighterOf(event.side, event.fighter);
        sfx("click");
        paint(who, fighter);
        await flash(who, "is-entering", 400);
        await say(event.side === 0 ? `¡Adelante, ${label(fighter)}!` : `${state.rival.name} saca a ${label(fighter)}.`);
      } else if (event.type === "move") {
        const attacker = fighterOf(event.side, event.fighter);
        await say(`¡${label(attacker)} usa ${event.move}!`, 650);
        await flash(who, "is-attacking", 300);
      } else if (event.type === "damage") {
        sfx(event.effectiveness === 0 ? "weak" : event.effectiveness >= 2 ? "super" : event.effectiveness < 1 ? "weak" : "hit");
        if (event.effectiveness > 0) await flash(who, "is-hit", 380);
        setHp(who, event.hp, event.max_hp);
        await sleep(500 * speed);
        const target = fighterOf(event.side, event.fighter);
        if (event.effectiveness === 0) await say(`No afecta a ${label(target)}…`);
        else if (event.critical) await say("¡Un golpe crítico!");
        if (event.effectiveness >= 2) await say("¡Es muy eficaz!");
        else if (event.effectiveness > 0 && event.effectiveness < 1) await say("No es muy eficaz…");
      } else if (event.type === "faint") {
        const target = fighterOf(event.side, event.fighter);
        sfx("faint");
        ui[who].sprite.classList.add("is-fainted");
        target.alive = false;
        paintBalls();
        await say(`¡${label(target)} se ha debilitado!`, 1100);
      } else if (event.type === "end") {
        sfx(event.winner === 0 ? "victory" : "defeat");
        await say(event.winner === 0 ? "¡Has ganado el combate!" : event.winner === null ? "El combate termina en empate." : "Has perdido el combate…", 1300);
      }
    }
  }

  // ── Controles ─────────────────────────────────────────────
  function lock(text = null) {
    for (const b of controls.querySelectorAll("button")) b.disabled = true;
    if (text) message.textContent = text;
  }

  async function act(action) {
    lock();
    status.textContent = "";
    try {
      await onAction(action);
    } catch (error) {
      showError(error);
      drawControls();
    }
  }

  /** Anima los sucesos de `next` y deja la arena en ese estado. Las llamadas se encolan. */
  function update(next) {
    queue = queue.then(async () => {
      // Los sucesos usan los datos de antes del turno; al final se sincroniza todo.
      await animate(next.events ?? []);
      state = next;
      status.textContent = "";
      paintAll();
      drawControls();
      if (state.status !== "active") loadProgress({ force: true }).catch(() => {});
    });
    return queue;
  }

  function drawControls() {
    forfeitButton.hidden = state.status !== "active";
    if (state.status !== "active") {
      controls.replaceChildren(result());
      return;
    }
    const current = state.you.fighters[state.you.active];
    message.textContent = `¿Qué debe hacer ${label(current)}?`;
    const moves = state.actions.moves.map((move) =>
      el(
        "button",
        { type: "button", class: `move ${typeClass(move.type)}`, onClick: () => act({ type: "move", index: move.index }) },
        el("span", { class: "move__name" }, move.name),
        el("span", { class: "move__meta" }, `${typeLabel(move.type)} · ${move.power}`),
        el("span", { class: `move__hint ${move.effectiveness >= 2 ? "is-good" : move.effectiveness < 1 ? "is-bad" : ""}` }, effectivenessHint(move.effectiveness)),
      ),
    );
    const switches = state.actions.switch.map((index) => {
      const f = state.you.fighters[index];
      return el(
        "button",
        { type: "button", class: "switch-btn", onClick: () => act({ type: "switch", to: index }) },
        el("img", { src: spriteUrl(f.pokemon_id), alt: "", width: 48, height: 48 }),
        el("span", {}, label(f), el("small", {}, `Nv. ${f.level} · ${f.hp}/${f.max_hp} PS`)),
      );
    });
    controls.replaceChildren(
      el("div", { class: "moves", role: "group", "aria-label": "Ataques" }, moves),
      switches.length ? el("details", { class: "switches" }, el("summary", {}, icon("refresh"), "Cambiar de Pokémon"), el("div", { class: "switches__list" }, switches)) : null,
    );
  }

  function result() {
    const won = state.status === "won";
    if (won) markDone("battle");
    // Sin rivales debilitados no hay experiencia (por ejemplo, si el rival abandona enseguida)
    const rewards = (state.rewards ?? []).filter((r) => r.xp_gained > 0 || r.evolves_to);
    return el(
      "div",
      { class: "battle-result" },
      el("h2", { class: `result result--${state.status}` }, icon(won ? "trophy" : "flag"), STATUS_TEXT[state.status]),
      rewards.length ? el("p", { class: "muted" }, "Experiencia ganada:") : state.rewards?.length ? el("p", { class: "muted" }, "Esta vez no hay experiencia: no se ha debilitado ningún Pokémon rival.") : null,
      el(
        "ul",
        { class: "rewards" },
        rewards.map((r) => {
          const up = r.level_after > r.level_before;
          const evolve = r.evolves_to ? evolveButton(r) : null;
          return el(
            "li",
            { class: "reward" },
            el("img", { src: spriteUrl(r.pokemon_id), alt: "", width: 56, height: 56 }),
            el("div", {}, el("strong", {}, displayName(r.name)), el("p", { class: "muted" }, `+${r.xp_gained} PX`), up ? el("p", { class: "level-up" }, icon("sparkles"), `¡Sube al nivel ${r.level_after}!`) : el("p", { class: "muted" }, `Nivel ${r.level_after}`)),
            evolve,
          );
        }),
      ),
      el(
        "div",
        { class: "row" },
        el("a", { class: "btn btn--primary", href: "#/combate" }, icon("bolt"), "Otro combate"),
        el("a", { class: "btn btn--secondary", href: "#/pokedex" }, icon("book"), "Mi Pokédex"),
      ),
    );
  }

  function evolveButton(reward) {
    const button = el("button", { type: "button", class: "btn btn--primary btn--small" }, icon("sparkles"), `Evolucionar a ${displayName(reward.evolves_to)}`);
    button.addEventListener("click", () =>
      busy(button, async () => {
        try {
          const entry = await request(`/api/v1/pokedex/${reward.entry_id}/evolve`, { method: "POST" });
          replaceEntry(entry);
          sfx("levelup");
          setTimeout(() => playCry(entry.pokemon_id), 600);
          toast(`¡${displayName(reward.name)} ha evolucionado a ${displayName(entry.pokemon_name)}!`, { type: "ok" });
          button.replaceWith(el("p", { class: "level-up" }, icon("sparkles"), `¡Ahora es ${displayName(entry.pokemon_name)}!`));
        } catch (error) {
          showError(error);
        }
      }, "Evolucionando…"),
    );
    return button;
  }

  forfeitButton.addEventListener("click", async () => {
    if (state.status !== "active") return;
    lock();
    try {
      await onForfeit();
    } catch (error) {
      showError(error);
      drawControls();
    }
  });

  view.replaceChildren(
    el("div", { class: "row row--between" }, el("a", { class: "back-link", href: "#/combate" }, icon("arrow-left"), "Combates"), el("div", { class: "row" }, fast, forfeitButton)),
    el("div", { class: "arena", role: "region", "aria-label": `Combate contra ${state.rival.name}` }, ui.rival.root, ui.you.root),
    el("div", { class: "battle-msg" }, message, status),
    controls,
  );
  paintAll();
  queue = (async () => {
    if (state.status === "active" && state.turn === 0) {
      await say(intro ?? `¡${state.rival.name} te desafía!`, 1200);
      playCry(state.rival.fighters[state.rival.active].pokemon_id, { volume: 0.4 });
    }
    drawControls();
  })();

  return {
    update,
    lock,
    say,
    redraw: () => queue.then(drawControls),
    setStatus: (text) => {
      status.textContent = text;
    },
    get state() {
      return state;
    },
  };
}

// ═════════════════════════════════════════════════════════════
//  Contra la máquina (HTTP)
// ═════════════════════════════════════════════════════════════
export async function renderBattle(view, { id }) {
  view.replaceChildren(loader("Entrando al estadio…"));
  const initial = await request(`/api/v1/battles/${id}`);
  const arena = createArena(view, initial, {
    onAction: async (action) => {
      await arena.update(await request(`/api/v1/battles/${id}/turn`, { method: "POST", json: action }));
    },
    onForfeit: async () => {
      const next = await request(`/api/v1/battles/${id}/forfeit`, { method: "POST" });
      await arena.say("Te has retirado del combate.");
      await arena.update({ ...next, events: [] });
    },
  });
  // Tras el combate, los niveles han cambiado: se recargan para Mi Pokédex
  if (initial.status !== "active") loadProgress({ force: true }).catch(() => {});
  loadCollection().catch(() => {});
}

// ═════════════════════════════════════════════════════════════
//  Contra un amigo (WebSocket)
// ═════════════════════════════════════════════════════════════
function inviteLink(code) {
  return `${window.location.origin}${window.location.pathname}#/combate/amigo/${code}`;
}

async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    toast("Copiado. Pásaselo a tu amigo.", { type: "ok" });
  } catch {
    toast("No se ha podido copiar: selecciónalo y cópialo a mano.", { type: "error" });
  }
}

function waitingScreen(code) {
  const link = inviteLink(code);
  const linkInput = el("input", { class: "input", id: "invite-link", value: link, readonly: true });
  linkInput.addEventListener("focus", () => linkInput.select());
  const copyLink = el("button", { type: "button", class: "btn btn--primary" }, icon("copy"), "Copiar enlace");
  copyLink.addEventListener("click", () => copyText(link));
  const copyCode = el("button", { type: "button", class: "btn btn--secondary btn--small" }, icon("copy"), "Copiar código");
  copyCode.addEventListener("click", () => copyText(code));
  return el(
    "section",
    { class: "panel stack waiting-room" },
    el("h1", {}, "Reto para un amigo"),
    el("p", {}, "Pásale el enlace o el código. Tiene que entrar con su cuenta y elegir su equipo."),
    el("div", { class: "room-code", "aria-label": `Código del reto: ${code.split("").join(" ")}` }, code),
    el("div", {}, copyCode),
    el("div", { class: "field" }, el("label", { for: "invite-link" }, "Enlace del reto"), el("div", { class: "input-row" }, linkInput, copyLink)),
    el("p", { class: "waiting-room__status", role: "status" }, el("span", { class: "pulse-dot", "aria-hidden": "true" }), "Esperando a tu rival… El reto caduca en 15 minutos."),
    el("p", { class: "muted" }, "Si cierras esta pantalla, el reto se cancela."),
    el("div", {}, el("a", { class: "btn btn--ghost", href: "#/combate" }, icon("x"), "Cancelar el reto")),
  );
}

function friendProblem(view, text) {
  view.replaceChildren(empty("users", "No se ha podido jugar", text, el("a", { class: "btn btn--primary", href: "#/combate" }, icon("arrow-left"), "Volver a Combate")));
}

export async function renderFriendBattle(view, { code }, ctx = {}) {
  const upper = code.toUpperCase();
  if (!CODE_PATTERN.test(upper)) {
    friendProblem(view, "Ese código no es válido: tiene 6 caracteres, letras y números.");
    return;
  }
  if (intents.has(upper)) {
    const team = intents.get(upper);
    intents.delete(upper);
    await playFriend(view, upper, team, ctx);
    return;
  }

  // Llega por un enlace: elige con qué equipo aceptar el reto
  view.replaceChildren(loader("Preparando el reto…"));
  const usable = usableTeams(await request("/api/v1/teams"));
  if (!usable.length) {
    view.replaceChildren(el("h1", {}, "Te han retado"), needTeam());
    return;
  }
  const state = { team: preferredTeam(usable) };
  const accept = el("button", { type: "button", class: "btn btn--primary btn--big" }, icon("swords"), "Aceptar el reto");
  accept.addEventListener("click", () => playFriend(view, upper, state.team, ctx));
  view.replaceChildren(
    el("a", { class: "back-link", href: "#/combate" }, icon("arrow-left"), "Combates"),
    el(
      "section",
      { class: "panel stack" },
      el("h1", { class: "row" }, icon("users"), "Te han retado"),
      el("p", {}, "Reto ", el("strong", { class: "room-code room-code--inline" }, upper), ". Elige tu equipo y empieza el combate en directo."),
      teamPicker(usable, state),
      el("div", {}, accept),
    ),
  );
}

async function playFriend(view, code, teamId, { isCurrent = () => true } = {}) {
  view.replaceChildren(loader("Conectando con el estadio…"));
  let token;
  try {
    token = await socketToken();
  } catch (error) {
    showError(error);
    return;
  }
  const scheme = window.location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${scheme}://${window.location.host}/api/v1/pvp/ws`);
  let arena = null;
  let over = false; // terminado, con error o abandonado: el cierre ya no es una sorpresa

  const send = (data) => {
    if (ws.readyState !== WebSocket.OPEN) throw new Error("Se ha perdido la conexión con el combate.");
    ws.send(JSON.stringify(data));
  };
  const warnBeforeLeaving = (event) => {
    event.preventDefault();
    event.returnValue = ""; // si cierras la pestaña en pleno combate, pierdes
  };
  const leave = () => {
    over = true;
    window.removeEventListener("beforeunload", warnBeforeLeaving);
    if (ws.readyState === WebSocket.CONNECTING || ws.readyState === WebSocket.OPEN) ws.close(1000);
  };
  window.addEventListener("hashchange", leave, { once: true });

  ws.addEventListener("open", () => {
    // El token va en el primer mensaje, nunca en la URL
    send({ type: "auth", token });
    send({ type: "join", code, team_id: teamId });
  });

  ws.addEventListener("message", (event) => {
    if (!isCurrent()) return;
    let msg;
    try {
      msg = JSON.parse(event.data);
    } catch {
      return;
    }
    switch (msg.type) {
      case "waiting":
        view.replaceChildren(waitingScreen(msg.code));
        break;
      case "start":
        sfx("click");
        window.addEventListener("beforeunload", warnBeforeLeaving);
        arena = createArena(view, msg, {
          onAction: async (action) => send({ type: "action", action }),
          onForfeit: async () => send({ type: "forfeit" }),
          intro: `¡Empieza el combate contra ${msg.rival.name}!`,
        });
        break;
      case "waiting_rival":
        arena?.lock(`Esperando a ${arena.state.rival.name}…`);
        break;
      case "rival_ready":
        arena?.setStatus(`${arena.state.rival.name} ya ha elegido.`);
        break;
      case "turn":
        arena?.update(msg);
        break;
      case "end":
        over = true;
        window.removeEventListener("beforeunload", warnBeforeLeaving);
        arena?.update(msg);
        break;
      case "rival_left":
        toast("Tu rival ha abandonado: ganas el combate.", { type: "ok" });
        break;
      case "error":
        if (arena && !over) {
          toast(msg.message, { type: "error" });
          arena.redraw();
        } else {
          over = true;
          friendProblem(view, msg.message);
        }
        break;
      default:
        break;
    }
  });

  ws.addEventListener("close", () => {
    window.removeEventListener("beforeunload", warnBeforeLeaving);
    if (over || !isCurrent()) return;
    over = true;
    friendProblem(view, arena ? "Se ha cortado la conexión con el combate." : "No se ha podido conectar con el reto. Puede que haya caducado.");
  });
}
