// Combates: preparación (equipo, dificultad, tipo del rival), arena animada turno a turno
// y resultado con experiencia, subidas de nivel y evoluciones.
// Toda la lógica la resuelve la API; aquí solo se anima lo que devuelve.

import { request } from "../api.js";
import { el, prefs, sleep } from "../dom.js";
import { TYPES, artworkUrl, backSpriteUrl, displayName, multiplierLabel, spriteUrl, typeClass, typeLabel } from "../format.js";
import { loadCollection, loadProgress, replaceEntry } from "../data.js";
import { icon } from "../icons.js";
import { markDone } from "../missions.js";
import { navigate } from "../router.js";
import { playCry, sfx } from "../sound.js";
import { busy, empty, loader, showError, toast, typeBadges } from "../ui.js";

const DIFFICULTIES = [
  ["easy", "Fácil"],
  ["normal", "Normal"],
  ["hard", "Difícil"],
];
const STATUS_TEXT = { won: "Victoria", lost: "Derrota", draw: "Empate", forfeit: "Abandonado", active: "En curso" };
const reducedMotion = () => window.matchMedia("(prefers-reduced-motion: reduce)").matches;

// ═════════════════════════════════════════════════════════════
//  Preparación
// ═════════════════════════════════════════════════════════════
export async function renderBattleLobby(view) {
  view.replaceChildren(loader("Preparando el estadio…"));
  const [teams, history] = await Promise.all([request("/api/v1/teams"), request("/api/v1/battles")]);
  const usable = teams.filter((t) => t.members.length);
  if (!usable.length) {
    view.replaceChildren(
      el("h1", {}, "Combate"),
      empty("swords", "Necesitas un equipo", "Crea un equipo con tus Pokémon y vuelve para combatir.", el("a", { class: "btn btn--primary", href: "#/equipos/nuevo" }, icon("plus"), "Crear un equipo")),
    );
    return;
  }

  const state = { team: usable[0].id, difficulty: prefs.get("battleDifficulty", "normal") };
  const teamButtons = usable.map((team) => {
    const button = el(
      "button",
      { type: "button", class: "team-pick", "aria-pressed": String(team.id === state.team) },
      el("span", { class: "team-pick__name" }, team.name),
      el("span", { class: "team-pick__members", "aria-hidden": "true" }, team.members.map((m) => el("img", { src: artworkUrl(m.pokemon_id), alt: "", width: 32, height: 32 }))),
    );
    button.addEventListener("click", () => {
      state.team = team.id;
      for (const b of teamButtons) b.setAttribute("aria-pressed", String(b === button));
    });
    return button;
  });
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

  view.replaceChildren(
    el("div", { class: "page-head" }, el("div", {}, el("h1", {}, "Combate"), el("p", {}, "Tu equipo contra un rival. Ganando, tus Pokémon suben de nivel y pueden evolucionar."))),
    el(
      "section",
      { class: "panel stack" },
      el("h2", {}, "1 · Elige tu equipo"),
      el("div", { class: "team-picks", role: "group", "aria-label": "Tu equipo" }, teamButtons),
      el("h2", {}, "2 · Elige el rival"),
      el("div", { class: "row" }, el("div", { class: "segmented segmented--3", role: "group", "aria-label": "Dificultad" }, difficultyButtons), el("div", { class: "field field--inline" }, el("label", { for: "battle-theme" }, "Tipo del rival"), theme)),
      el("div", {}, go),
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
                el("a", { href: `#/combate/${b.id}` }, el("span", { class: `result result--${b.status}` }, STATUS_TEXT[b.status] ?? b.status), b.opponent_name),
              ),
            ),
          ),
        )
      : null,
  );
}

// ═════════════════════════════════════════════════════════════
//  Arena
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

export async function renderBattle(view, { id }) {
  view.replaceChildren(loader("Entrando al estadio…"));
  let state = await request(`/api/v1/battles/${id}`);
  let speed = prefs.get("battleFast", false) ? 0.45 : 1;

  // ── Elementos de la arena ─────────────────────────────────
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
  async function act(action) {
    for (const b of controls.querySelectorAll("button")) b.disabled = true;
    try {
      const next = await request(`/api/v1/battles/${id}/turn`, { method: "POST", json: action });
      // Los sucesos usan los datos de antes del turno; al final se sincroniza todo.
      await animate(next.events);
      state = next;
      paintAll();
      drawControls();
      if (state.status !== "active") loadProgress({ force: true }).catch(() => {});
    } catch (error) {
      showError(error);
      drawControls();
    }
  }

  function drawControls() {
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
    const rewards = state.rewards ?? [];
    return el(
      "div",
      { class: "battle-result" },
      el("h2", { class: `result result--${state.status}` }, icon(won ? "trophy" : "flag"), STATUS_TEXT[state.status]),
      rewards.length ? el("p", { class: "muted" }, "Experiencia ganada:") : null,
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
    try {
      state = await request(`/api/v1/battles/${id}/forfeit`, { method: "POST" });
      await say("Te has retirado del combate.");
      drawControls();
    } catch (error) {
      showError(error);
    }
  });

  view.replaceChildren(
    el("div", { class: "row row--between" }, el("a", { class: "back-link", href: "#/combate" }, icon("arrow-left"), "Combates"), el("div", { class: "row" }, fast, forfeitButton)),
    el("div", { class: "arena", role: "region", "aria-label": `Combate contra ${state.rival.name}` }, ui.rival.root, ui.you.root),
    el("div", { class: "battle-msg" }, message),
    controls,
  );
  paintAll();
  if (state.status === "active" && state.turn === 0) {
    await say(`¡${state.rival.name} te desafía!`, 1200);
    playCry(state.rival.fighters[state.rival.active].pokemon_id, { volume: 0.4 });
  }
  drawControls();
  // Tras el combate, los niveles han cambiado: se recargan para Mi Pokédex
  if (state.status !== "active") loadProgress({ force: true }).catch(() => {});
  loadCollection().catch(() => {});
}
