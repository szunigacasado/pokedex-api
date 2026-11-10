// La Pokédex que habla: lee en voz alta con la síntesis de voz del navegador (sin
// servicios externos ni coste) y reproduce el grito de cada Pokémon.

const CRIES = "https://raw.githubusercontent.com/PokeAPI/cries/main/cries/pokemon/latest";

export function cryUrl(id) {
  return `${CRIES}/${id}.ogg`;
}

export function canSpeak() {
  return "speechSynthesis" in window && "SpeechSynthesisUtterance" in window;
}

function spanishVoice() {
  const voices = window.speechSynthesis.getVoices();
  return voices.find((v) => v.lang === "es-ES") ?? voices.find((v) => v.lang?.startsWith("es")) ?? null;
}

function lens(on) {
  document.getElementById("lens")?.classList.toggle("lens--pulse", on);
}

export function stopSpeaking() {
  if (canSpeak()) window.speechSynthesis.cancel();
  lens(false);
}

/** Lee `text` en español. La lente de la Pokédex se ilumina mientras habla. */
export function speak(text, { onEnd } = {}) {
  if (!canSpeak()) return false;
  stopSpeaking();
  const utterance = new SpeechSynthesisUtterance(text);
  utterance.lang = "es-ES";
  utterance.rate = 1.02;
  utterance.pitch = 0.9; // un punto robótico, como un aparato
  const voice = spanishVoice();
  if (voice) utterance.voice = voice;
  utterance.addEventListener("start", () => lens(true));
  const finish = () => {
    lens(false);
    onEnd?.();
  };
  utterance.addEventListener("end", finish);
  utterance.addEventListener("error", finish);
  window.speechSynthesis.speak(utterance);
  return true;
}

let currentCry = null;

/** Reproduce el grito. Devuelve false si el navegador no puede (Safari no lee OGG). */
export async function playCry(id, { volume = 0.5 } = {}) {
  currentCry?.pause();
  const audio = new Audio(cryUrl(id));
  audio.volume = volume;
  currentCry = audio;
  try {
    await audio.play();
    return true;
  } catch {
    return false;
  }
}

// Al cambiar de pantalla, la Pokédex se calla.
window.addEventListener("hashchange", () => {
  stopSpeaking();
  currentCry?.pause();
});
