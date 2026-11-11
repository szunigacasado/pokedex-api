// Botones de la ficha: escuchar la entrada de la Pokédex y el grito del Pokémon.

import { el } from "../dom.js";
import { displayName } from "../format.js";
import { toast } from "../ui.js";
import { canSpeak, playCry, speak, stopSpeaking } from "../voice.js";
import { icon } from "../icons.js";

export function voiceButtons(detail, slot) {
  const species = detail.species;
  const name = species?.display_name ?? displayName(detail.name);

  if (canSpeak() && species?.description) {
    let speaking = false;
    const button = el("button", { type: "button", class: "btn btn--secondary", "aria-pressed": "false" }, icon("volume"), "Escuchar su entrada");
    const reset = () => {
      speaking = false;
      button.setAttribute("aria-pressed", "false");
      button.replaceChildren(icon("volume"), "Escuchar su entrada");
    };
    button.addEventListener("click", () => {
      if (speaking) {
        stopSpeaking();
        reset();
        return;
      }
      const text = [name, species.genus, species.description].filter(Boolean).join(". ");
      if (speak(text, { onEnd: reset })) {
        speaking = true;
        button.setAttribute("aria-pressed", "true");
        button.replaceChildren(icon("stop"), "Parar");
      }
    });
    slot.append(button);
  }

  const cry = el("button", { type: "button", class: "btn btn--secondary" }, icon("music"), "Su grito");
  cry.addEventListener("click", async () => {
    if (!(await playCry(detail.id))) {
      toast("Este navegador no puede reproducir el grito (formato OGG).", { type: "warn" });
    }
  });
  slot.append(cry);
}
