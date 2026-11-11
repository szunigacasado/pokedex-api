// «Adivina la silueta»: 10 rondas, cuatro opciones. Al acertar, el Pokémon se captura en
// tu Pokédex y suena su grito.

import { el, prefs } from "../dom.js";
import { artworkUrl, displayName, plural } from "../format.js";
import { ROUNDS, newRound, verdict } from "../game.js";
import { addToCollection, entryFor, loadCollection, loadKanto, updateEntry } from "../data.js";
import { markDone } from "../missions.js";
import { loader, showError, toast } from "../ui.js";
import { playCry } from "../voice.js";

async function capture(pokemon) {
  const entry = entryFor(pokemon.id);
  if (entry?.is_captured) return `${displayName(pokemon.name)} ya estaba en tu Pokédex.`;
  if (entry) await updateEntry(entry.id, { is_captured: true });
  else await addToCollection(pokemon.id, { is_captured: true });
  return `¡${displayName(pokemon.name)} capturado y guardado en tu Pokédex!`;
}

export async function renderGame(view) {
  view.replaceChildren(loader("Preparando las siluetas…"));
  const [catalog] = await Promise.all([loadKanto(), loadCollection()]);

  let round = 0;
  let score = 0;
  const used = new Set();
  const best = prefs.get("gameBest", 0);

  const status = el("p", { class: "game__status", role: "status" });
  const stage = el("div", { class: "game__stage" });
  const options = el("div", { class: "game__options", role: "group", "aria-label": "¿Quién es?" });
  const feedback = el("div", { class: "game__feedback", "aria-live": "assertive" });
  const next = el("button", { type: "button", class: "btn btn--primary", hidden: true }, "Siguiente ▶");

  function finish() {
    const record = score > best;
    if (record) prefs.set("gameBest", score);
    stage.replaceChildren(el("div", { class: "game__result" }, el("p", { class: "game__score" }, `${score} / ${ROUNDS}`), el("p", {}, verdict(score)), record ? el("p", { class: "ok" }, "🏆 ¡Nuevo récord!") : el("p", { class: "muted" }, `Tu récord: ${Math.max(best, score)} / ${ROUNDS}`)));
    options.replaceChildren(el("button", { type: "button", class: "btn btn--primary", onClick: () => renderGame(view) }, "🔄 Jugar otra vez"), el("a", { class: "btn btn--secondary", href: "#/pokedex" }, "📕 Ver mi Pokédex"));
    feedback.replaceChildren();
    next.hidden = true;
    status.textContent = "Partida terminada.";
    options.querySelector("button")?.focus();
  }

  function play() {
    if (round >= ROUNDS) return finish();
    round += 1;
    const { answer, options: choices } = newRound(catalog, { used });
    used.add(answer.id);
    status.textContent = `Ronda ${round} de ${ROUNDS} · ${plural(score, "acierto")}`;
    feedback.replaceChildren();
    next.hidden = true;

    // El texto alternativo no revela la respuesta.
    const img = el("img", { class: "game__img", src: artworkUrl(answer.id), alt: "Silueta de un Pokémon misterioso", width: 300, height: 300 });
    stage.replaceChildren(img);

    const buttons = choices.map((choice) =>
      el("button", { type: "button", class: "btn btn--secondary game__option" }, displayName(choice.name)),
    );
    buttons.forEach((button, i) =>
      button.addEventListener("click", async () => {
        for (const b of buttons) b.disabled = true;
        const right = choices[i].id === answer.id;
        img.classList.add("is-revealed");
        img.alt = displayName(answer.name);
        buttons[choices.indexOf(answer)].classList.add("game__option--right");
        if (!right) button.classList.add("game__option--wrong");
        playCry(answer.id, { volume: 0.4 });
        if (right) {
          score += 1;
          markDone("guess");
          status.textContent = `Ronda ${round} de ${ROUNDS} · ${plural(score, "acierto")}`;
          let message = `¡Correcto! Es ${displayName(answer.name)}.`;
          try {
            message = `¡Correcto! ${await capture(answer)}`;
          } catch (error) {
            showError(error);
          }
          feedback.replaceChildren(el("p", { class: "alert alert--ok" }, message));
        } else {
          feedback.replaceChildren(el("p", { class: "alert" }, `¡Oh, no! Era ${displayName(answer.name)}.`));
        }
        next.textContent = round >= ROUNDS ? "Ver resultado ▶" : "Siguiente ▶";
        next.hidden = false;
        next.focus();
      }),
    );
    options.replaceChildren(...buttons);
    buttons[0].focus();
  }

  next.addEventListener("click", play);

  view.replaceChildren(
    el("div", { class: "page-head" }, el("div", {}, el("h1", {}, "Adivina la silueta"), el("p", {}, "¿Sabes quién es? Si aciertas, lo capturas para tu Pokédex."))),
    el("div", { class: "game panel" }, status, stage, options, feedback, el("div", { class: "row" }, next)),
  );
  play();
  if (!prefs.get("gameIntroShown", false)) {
    prefs.set("gameIntroShown", true);
    toast("Pista: fíjate en la forma de la cabeza y la cola.", { timeout: 5000 });
  }
}
